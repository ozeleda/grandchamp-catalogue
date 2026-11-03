#!/usr/bin/env python3
"""Build the Grandchamp catalogue SQLite database from the scanned volumes.

Usage:
    python3 scripts/build_db.py [--config volumes.json] [--out catalogue.sqlite]

Requires `pdftotext` (poppler-utils). Uses only the Python standard library.

The volumes are Internet Archive scans that already carry an OCR text layer, so
the script reads that layer with `pdftotext -layout` and parses:

  * Part I  - "Analyse des actes passés en chancellerie": one row per act
              (date, summary, language, condition, officials in office...).
  * Parts II/III - documents copied in extenso: one row per document, with
              the title/date from the table of contents and the full OCR text.
"""
import argparse
import difflib
import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_parties

ROOT = Path(__file__).resolve().parent.parent

MONTHS = {
    "janv": 1, "jan": 1, "janvier": 1,
    "fév": 2, "févr": 2, "fevr": 2, "fev": 2, "février": 2,
    "mars": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7, "juill": 7, "juillet": 7,
    "août": 8, "aout": 8,
    "sept": 9, "sep": 9, "septembre": 9,
    "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11,
    "déc": 12, "dec": 12, "décembre": 12,
}
MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
               "août", "septembre", "octobre", "novembre", "décembre"]

DITTO_RE = re.compile(r"^[—–\-~=_]{1,2}$")
MONTH_ABBR = ["janv", "fév", "mars", "avril", "mai", "juin", "juil", "août", "aout",
              "sept", "oct", "nov", "déc"]
MONTH_NUM = {"janv": 1, "fév": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juil": 7,
             "août": 8, "aout": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12}
# Start of an entry in the date column. OCR mangles the day ("i5", "IG", "Bjanv.",
# "2-4") and the month ("cet.", "jiiil.", "cléc."), so this is deliberately loose;
# match_entry() then validates the pieces.
ENTRY_RE = re.compile(
    r"^(?P<indent> {0,6})(?P<br>\[)?"
    r"(?:(?P<sd>[s3]\.\s?d\.)[-.]?"
    r"|(?P<day>1er|[0-9iIlOBGSU?'\\)*\"«°”’\-]{1,4}?)\s*"
    r"(?P<month>\.?[a-zéû]{2,5}(?: [a-z])?[.;,\"’]*|juillet|janvier|février|avril|septembre|octobre|novembre|décembre|[—–\-~=_]{1,2})\]?"
    r"|(?P<dayonly>[0-9]{1,2}(?:er)?)(?=\s{4,}[A-ZÉ«(]))"
    r"(?=\s|$)\s*(?P<rest>.*)$"
)
YEAR_RE = re.compile(r"^\s{0,12}(?P<y>[1iI][5S6][0-9OÔoiflS]{2,3})(?:\s*$|\s{5,}(?P<rest>\S.*)$)")
PAGE_HDR_RE = re.compile(r"^\s{8,}[-—–~]?\s*([0-9]{1,3})\s*[-—–~.]*\s*$")
FOOTNOTE_RE = re.compile(r"^\s*\((\d{1,2})\)\s+\S")
# A bold heading on its own line, e.g. "Daniel de Mussan, chancelier (1)".
OFFICIAL_RE = re.compile(
    r"^\s{15,}(?P<name>[A-ZÀ-Ý][A-Za-zÀ-ÿ'’ .\-]{2,50}?)[\s?{}().]*[,.]\s*"
    r"(?P<role>vice-consul|consul|chancelier)\s*(?:\(\d\))?\s*\.?\s*$"
)
SIGNATURE_RE = re.compile(r"^\s*signature\s+de\b", re.I)

ROLE_KEYS = {"consul": "consul", "vice-consul": "vice_consul", "chancelier": "chancelier"}


def pdf_page_text(pdf, page):
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
        check=True, capture_output=True, text=True,
    ).stdout
    return out.replace("\f", "")


def clean_ocr(text):
    """Remove gutter specks from Tesseract layout text and dedent the page so the
    date column starts at column 0, as in pdftotext output."""
    out = []
    for l in text.split("\n"):
        if len(l.strip()) > 12:
            # specks at the end of a line
            l = re.sub(r"(?:\s+(?:[|\\/.:;,'‘’`~_^°»«{}]+|[A-ZÈÉÌÎÏÀÂÄÆ€]|\d))+\s*$", "", l)
            # a short token far left of the text that does not start a dated entry
            # junk in front of a real date: "I {i   29  —  En italien", "- 4 janv.", "1 20 —"
            m = re.match(r"^(\s*(?:[^\w\s]+|\w{1,2}[^\w\s]*)(?:\s+[^\w\s]{1,3})?\s+)"
                         r"(?=\d{1,2}(?:er)?\s+(?:[—–-]|janv|f[ée]v|mars|avril|mai|juin|juil|ao[uû]t|sept|oct|nov|d[ée]c)\S*\s{2,})", l)
            if m and not match_entry(l.lstrip(), {}, 0, True):
                l = " " * len(m.group(1)) + l[m.end():]
            # a short token far left of the text that does not start a dated entry
            m = re.match(r"^(\s*)(\S{1,4})(\s{3,})(?=\S)", l)
            if (m and not match_entry(l.lstrip(), {}, 0, True) and not YEAR_RE.match(l)
                    and not norm_month(m.group(2)) and not re.fullmatch(r"[sS]\.\s?d\.", m.group(2))):
                l = " " * m.end() + l[m.end():]
        elif re.fullmatch(r"\s*[|\\/.:;,'‘’`~_^°»«{}A-Za-zÈÉÌÎÏÀÂÄ\d]{1,3}\s*", l):
            l = ""                      # isolated speck
        out.append(l.rstrip())
    # the margin is where dated entries and year headings start; junk at the edge is ignored
    dated = sorted(len(l) - len(l.lstrip()) for l in out
                   if l.strip() and (match_entry(l.lstrip(), {}, 0, True) or YEAR_RE.match(l.strip())))
    if dated:
        cut = dated[0] if len(dated) < 3 else dated[1]
    else:
        long_ = sorted(len(l) - len(l.lstrip()) for l in out if len(l.strip()) >= 25)
        cut = max(0, long_[len(long_) // 10] - 9) if long_ else 0
    # short junk lines left of the margin
    out = ["" if (len(l) - len(l.lstrip()) < cut - 2 and len(l.strip()) <= 8) else l for l in out]
    return "\n".join(l[cut:] if len(l) - len(l.lstrip()) >= cut else l.lstrip() for l in out)


def noisy(vol):
    """Volumes read from an OCR'd photocopy or a Google OCR layer, where dates can be lost."""
    return bool(vol.get("text_source") or (vol.get("pages_dir") and not vol.get("clean_scan")))


def printed_text(vol, printed):
    """Layout text of a printed page (from text/vol<N>/pages when present)."""
    if vol.get("pages_dir"):
        return page_text(vol, printed)[1]
    return pdf_page_text(ROOT / vol["file"], printed + vol["page_offset"])


def page_text(vol, printed):
    """(pdf_page, layout text) of a printed page: from text/vol<N>/pages (written by
    extract_pages.py from the scan), from the old Tesseract cache, or from the PDF."""
    if vol.get("pages_dir"):
        src = ROOT / vol["pages_dir"]
        f = src / f"p{printed:03d}.txt"
        if not f.exists():
            return None, ""
        idx = json.loads((src / "index.json").read_text())
        return idx[f.stem]["pdf_page"], f.read_text(encoding="utf-8")
    if vol.get("text_source"):
        src = ROOT / vol["text_source"]
        f = src / f"p{printed:03d}.txt"
        if not f.exists():
            return None, ""
        idx = json.loads((src / "index.json").read_text())
        text = f.read_text(encoding="utf-8")
        return idx[str(printed)]["pdf_page"], clean_ocr(text)
    pdf_page = printed + vol["page_offset"]
    return pdf_page, pdf_page_text(ROOT / vol["file"], pdf_page)


def norm_day(tok):
    if tok == "U":
        return 11
    t = tok.replace("i", "1").replace("I", "1").replace("l", "1").replace("O", "0")
    t = t.replace("B", "8").replace("G", "6").replace("S", "5").replace("*", "")
    t = re.sub(r"(er|\"|«|°|”|’|')$", "", t)
    if "-" in t:  # "2-4" is an OCR rendering of "24"
        t = t.replace("-", "")
    try:
        d = int(t)
    except ValueError:
        return None
    return d if 1 <= d <= 31 else None


def norm_year(tok, previous, vol, page, reg=None):
    fix = vol.get("year_fixes", {}).get(f"{page}:{tok}")
    if fix:
        return fix
    src = reg or vol
    lo = int((src.get("date_start") or vol["period_start"])[:4])
    hi = int((src.get("date_end") or vol["period_end"])[:4])
    digits = tok[2:4].replace("O", "0").replace("o", "0").replace("Ô", "0").replace("i", "1")
    try:
        last2 = int(digits)
    except ValueError:
        return previous
    year = 1600 + last2 if last2 < 50 else 1500 + last2
    if not lo <= year <= hi:
        print(f"  ! year {tok!r} on page {page} out of range, keeping {previous}", file=sys.stderr)
        return previous
    return year


def norm_month(tok, strict=False):
    key = re.sub(r"[^a-zéû]", "", tok.lower())
    if key in MONTHS:
        return MONTHS[key]
    if strict and not tok.rstrip().endswith("."):
        return None                      # OCR text: a fuzzy month must at least keep its period
    best = difflib.get_close_matches(key, MONTH_ABBR, n=1, cutoff=0.75 if strict else 0.5)
    return MONTH_NUM[best[0]] if best else None


def match_entry(line, day_fixes, page, strict=False):
    """Return the parsed start of an act, or None if the line is not one.
    strict (OCR text): the day must be written with digits and the month spelled closely."""
    m = ENTRY_RE.match(line)
    if not m:
        return None
    if strict and m.group("day") and not re.fullmatch(r"\d{1,2}(?:er|°)?|1\W{1,2}", m.group("day")):
        return None
    if strict == True and m.group("dayonly"):
        return None
    if m.group("sd"):
        return {"undated": True, "day_tok": None, "month": None, "ditto": False,
                "bracket": bool(m.group("br")), "m": m}
    if m.group("dayonly"):
        return {"undated": False, "day_tok": m.group("dayonly"), "month": None, "ditto": True,
                "bracket": False, "m": m}
    day_tok, mtok = m.group("day"), m.group("month")
    if mtok == "er" and day_tok == "1":          # "1er" alone: the 1st, same month as above
        return {"undated": False, "day_tok": "1er", "month": None, "ditto": True,
                "bracket": bool(m.group("br")), "m": m}
    # Accept the day if it parses, has a manual fix, or is a short damaged token
    # (kept with day = NULL); reject longer tokens such as "240 écus" or "1593 roi".
    if (norm_day(day_tok) is None and f"{page}:{day_tok}" not in day_fixes
            and len(day_tok) > 2):
        return None
    if DITTO_RE.match(mtok):
        return {"undated": False, "day_tok": day_tok, "month": None, "ditto": True,
                "bracket": bool(m.group("br")), "m": m}
    mon = norm_month(mtok, strict)
    if mon is None and strict == "hathi" and re.match(r"\s{3,}\S", line[m.end("month"):]):
        mon = norm_month(mtok + ".", True)      # Google OCR: "20 ma", "14 jun", "1er uil,"
    if mon is None:
        return None
    return {"undated": False, "day_tok": day_tok, "month": mon, "ditto": False,
            "bracket": bool(m.group("br")), "m": m}


# --------------------------------------------------------------------------
# Part I: analysis of the acts
# --------------------------------------------------------------------------

def iter_analysis_lines(pdf, vol):
    """Yield (printed_page, pdf_page, line) for body lines of the analysis, and
    collect footnotes separately."""
    first, last = vol["analysis_pages"]
    footnotes = []
    for printed in range(first, last + 1):
        pdf_page, text = page_text(vol, printed)
        if pdf_page is None:
            print(f"  ! page {printed} missing from the scan", file=sys.stderr)
            continue
        lines = text.split("\n")
        in_note = False
        note_buf = None
        prev_line = ""
        for line in lines:
            if not line.strip():
                continue
            if PAGE_HDR_RE.match(line):
                m = PAGE_HDR_RE.match(line)
                if int(m.group(1)) != printed and not noisy(vol):
                    print(f"  ! page header {m.group(1)} on printed page {printed} (pdf {pdf_page})",
                          file=sys.stderr)
                continue
            fm = FOOTNOTE_RE.match(line)
            # register IV prints folio numbers "(19)" where footnotes print "(1)": a folio
            # follows the act's language note, a footnote is a low number on its own line
            if fm and (int(fm.group(1)) > 5 or re.search(r"En \w+\.\s*[—–]\s*$", prev_line)):
                fm = None
            prev_line = line
            if fm and line.strip().startswith("("):
                in_note = True
                note_buf = {"page": printed, "pdf_page": pdf_page,
                            "marker": int(fm.group(1)), "text": line.strip()}
                footnotes.append(note_buf)
                continue
            if in_note:
                note_buf["text"] += " " + line.strip()
                continue
            yield printed, pdf_page, line
    iter_analysis_lines.footnotes = footnotes


def is_noise(line):
    s = line.strip()
    if SIGNATURE_RE.match(s):
        return True
    letters = sum(c.isalpha() for c in s)
    indent = len(line) - len(line.lstrip())
    # Short, heavily indented fragments are OCR noise from facsimile images.
    if indent >= 20 and len(s) < 30 and not OFFICIAL_RE.match(line):
        return True
    if letters < max(3, len(s) * 0.4) and not (indent < 20 and re.search(r"\d", s)):
        return True
    return False


def join_text(parts):
    text = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if text.endswith("-") and p[:1].islower():
            text = text[:-1] + p          # rejoin hyphenated word
        elif text:
            text += " " + p
        else:
            text = p
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_analysis(pdf, vol):
    registers = sorted(vol["registers"], key=lambda r: r["start_page"])
    acts, officials, notes = [], [], []
    year = int(vol["period_start"][:4])
    month = None
    current = None
    in_office = {"consul": None, "vice_consul": None, "chancelier": None}
    pending_note = None   # editorial paragraph not attached to an act yet
    started = False

    def register_for(page):
        reg = registers[0]
        for r in registers:
            if page >= r["start_page"]:
                reg = r
        return reg

    def split_embedded(act):
        """OCR volumes: an act start ("En italien. —") inside a summary means the date of
        the following act was garbled; split it off and salvage the date if possible."""
        s = act["summary"]
        cuts = [m for m in re.finditer(r"(?P<pre>(?:\S{1,6}\s+){0,2})E[nt]i?\s+it\S*\s*[.,:]?\s*[—–-]", s)
                if m.start() > 15]
        if not cuts:
            return [act]
        out, start = [], 0
        for m in cuts:
            pre = m.group("pre")
            # keep a real last word of the sentence before (e.g. "Même", "clause.") in the previous act
            if pre and not re.search(r"\d|[—–-]|janv|f[ée]v|mars|avril|mai|juin|juil|ao[uû]t|sept|oct|nov|d[ée]c", pre):
                pre_start = m.start("pre") + len(pre)
            else:
                pre_start = m.start("pre")
            out.append(s[start:pre_start].strip())
            start = pre_start
        out.append(s[start:].strip())
        acts_out = [dict(act, summary=out[0])]
        prev = acts_out[0]
        for piece in out[1:]:
            mm = re.match(r"^(?P<pre>.*?)(?=E[nt]i?\s+it)", piece)
            pre = mm.group("pre") if mm else ""
            new = dict(act, summary=piece[len(pre):].strip(), date_supplied=False, undated=False)
            dm = re.search(r"(\d{1,2})", pre)
            mon = next((norm_month(t, True) for t in pre.split() if norm_month(t, True)), None)
            new["month"] = mon or prev["month"]
            if mon and prev["month"] and prev["month"] >= 11 and mon <= 2:
                new["year"] = prev["year"] + 1
            day = int(dm.group(1)) if dm and 1 <= int(dm.group(1)) <= 31 else None
            new["day"] = day if day else prev["day"]
            new["day_guess"] = not (day and (mon or "—" in pre or "-" in pre))
            new["date_raw"] = pre.strip() or "(date illegible in scan)"
            if not new["date_raw"].strip("( )"):
                new["date_raw"] = "(date illegible in scan)"
            if new["day_guess"] and not pre.strip():
                new["date_raw"] = "(date illegible in scan)"
            acts_out.append(new)
            prev = new
        return acts_out

    def flush():
        nonlocal current
        if current:
            current["summary"] = join_text(current.pop("lines"))
            if noisy(vol) and current["summary"]:
                pieces = split_embedded(current)
                for piece in pieces[:-1]:
                    acts.append(piece)
                current = pieces[-1]
            # A bare "s. d." line followed by an editor-supplied "[date]" entry is one act;
            # a scrap of a few characters is OCR debris split off the previous act.
            if (len(current["summary"]) < 20 and acts and noisy(vol)
                    and not re.match(r"(?:En \w+\. — )?(?:\(\d+\) )?M[êe]me (?:acte|affaire|sujet)", current["summary"])):
                acts[-1]["summary"] = (acts[-1]["summary"] + " " + current["summary"]).strip()
            elif current["summary"]:
                acts.append(current)
        current = None

    last_register = None
    month_year = None
    last_day = 0
    day_guess = False
    year_seen = False
    for printed, pdf_page, line in iter_analysis_lines(pdf, vol):
        reg = register_for(printed)
        if reg is not last_register:
            flush()
            last_register = reg
            started = False
            month = None
        ym = YEAR_RE.match(line)
        if ym:
            year = norm_year(ym.group("y"), year, vol, printed, reg)
            year_seen = True
            started = True
            if ym.group("rest") and current:
                current["lines"].append(ym.group("rest"))
            continue
        om = OFFICIAL_RE.match(line)
        if om:
            name = om.group("name").strip()
            role = ROLE_KEYS[om.group("role").lower()]
            officials.append({"name": name, "role": role, "page": printed,
                              "pdf_page": pdf_page, "register": reg["number"],
                              "after_act_seq": len(acts) + (1 if current else 0)})
            flush()
            in_office[role] = name
            if role == "consul":
                in_office["vice_consul"] = None if in_office["vice_consul"] == name else in_office["vice_consul"]
            continue
        lone = re.match(r"^\s{0,3}(?P<tok>[—–-]{1,3}|\?(?:\s+(?P<m>[a-zéû]{3,9})\.?)?(?:\s+[—–-])?)\s{3,}(?P<rest>(?:En \w+\. — |\(|[A-ZÉ«]).*)$", line)
        if started and noisy(vol) and vol.get("pages_dir") and lone:
            # "—" alone: same date as the act above; "?" : the day is not known
            flush()
            unknown = lone.group("tok").startswith("?")
            if lone.group("m") and norm_month(lone.group("m") + ".", True):
                month = norm_month(lone.group("m") + ".", True)
            current = {
                "register": reg["number"], "year": year, "month": month,
                "day": None if unknown else (last_day or None), "undated": False, "date_supplied": False,
                "date_raw": lone.group("tok"), "page": printed, "pdf_page": pdf_page,
                "lines": [lone.group("rest")], "day_guess": False,
                "consul": in_office["consul"], "vice_consul": in_office["vice_consul"],
                "chancelier": in_office["chancelier"],
            }
            year_seen = False
            continue
        em = (match_entry(line, vol.get("day_fixes", {}), printed,
                          "hathi" if vol.get("pages_dir") and noisy(vol) else bool(noisy(vol)))
              if started else None)
        if em:
            flush()
            undated = em["undated"]
            day = None
            if not undated:
                tok = em["day_tok"]
                day = vol.get("day_fixes", {}).get(f"{printed}:{tok}") or norm_day(tok)
                day_guess = False
                if day is None and re.fullmatch(r"\d\d", tok) and noisy(vol):
                    # OCR misread of the first digit ("40" for 10): pick the reading that
                    # follows the previous day in the same month
                    cands = [int(c + tok[1]) for c in "0123" if 1 <= int(c + tok[1]) <= 31]
                    prev = last_day if (em["ditto"] or em["month"] == month) else 0
                    later = [c for c in cands if c >= (prev or 0)]
                    day = min(later) if later else max(cands)
                    day_guess = True
                if day is None:
                    print(f"  ? p.{printed}: unreadable day {tok!r}", file=sys.stderr)
                last_day = day or last_day
                if not em["ditto"]:
                    if (noisy(vol) and month and month >= 11 and em["month"] <= 2
                            and not year_seen and year == month_year and year < int(reg["date_end"][:4])):
                        year += 1          # the new year's heading was lost in the scan
                    month, month_year = em["month"], year
            m = em["m"]
            current = {
                "register": reg["number"], "year": year, "month": None if undated else month,
                "day": day, "undated": undated, "date_supplied": em["bracket"],
                "date_raw": line[: m.start("rest")].strip(),
                "page": printed, "pdf_page": pdf_page, "lines": [m.group("rest")],
                "day_guess": (not undated) and day_guess,
                "consul": in_office["consul"], "vice_consul": in_office["vice_consul"],
                "chancelier": in_office["chancelier"],
            }
            year_seen = False
            continue
        mo = re.match(rf"^\s{{0,6}}(?P<m>{'|'.join(MONTH_ABBR)})\.?\s{{2,}}(?P<rest>[A-ZÉ«(].*)$", line)
        if started and noisy(vol) and mo and norm_month(mo.group("m"), True):
            # month without its day (the day was lost in the scan)
            flush()
            new_m = norm_month(mo.group("m"), True)
            if (month and month >= 11 and new_m <= 2 and not year_seen and year == month_year
                    and year < int(reg["date_end"][:4])):
                year += 1
            month, month_year = new_m, year
            current = {
                "register": reg["number"], "year": year, "month": month, "day": None,
                "undated": False, "date_supplied": False, "date_raw": line[:mo.start("rest")].strip(),
                "page": printed, "pdf_page": pdf_page, "lines": [mo.group("rest")], "day_guess": False,
                "consul": in_office["consul"], "vice_consul": in_office["vice_consul"],
                "chancelier": in_office["chancelier"],
            }
            year_seen = False
            continue
        lost = re.match(r"^\s{0,3}\.{4,}\s{2,}(?P<rest>\S.*)$", line)
        if started and noisy(vol) and lost:
            # the book prints dots where the register's date is torn or effaced
            flush()
            current = {
                "register": reg["number"], "year": year, "month": month,
                "day": last_day or None, "undated": False, "date_supplied": False,
                "date_raw": "........ (date effaced in the register)", "page": printed, "pdf_page": pdf_page,
                "lines": [lost.group("rest")], "day_guess": True,
                "consul": in_office["consul"], "vice_consul": in_office["vice_consul"],
                "chancelier": in_office["chancelier"],
            }
            year_seen = False
            continue
        if (started and current and noisy(vol)
                and re.match(r"^\s*E[nt]i?\s+it\S*\s*[.,:]?\s*[—–-]", line)
                and join_text(current["lines"]).rstrip().endswith((".", ")"))):
            # an act whose date column was lost in the scan: same date as the previous act
            flush()
            current = {
                "register": reg["number"], "year": year, "month": month,
                "day": last_day or None, "undated": False, "date_supplied": False,
                "date_raw": "(date illegible in scan)", "page": printed, "pdf_page": pdf_page,
                "lines": [line.strip()], "day_guess": True,
                "consul": in_office["consul"], "vice_consul": in_office["vice_consul"],
                "chancelier": in_office["chancelier"],
            }
            year_seen = False
            continue
        if not started or is_noise(line):
            continue
        if len(line) - len(line.lstrip()) <= 3 and current:
            print(f"  ? p.{printed}: line in date column not parsed as a date: {line.strip()[:70]!r}",
                  file=sys.stderr)
        if current:
            current["lines"].append(line)
    flush()
    return acts, officials, iter_analysis_lines.footnotes


# --------------------------------------------------------------------------
# Enrichment of act summaries (keyword heuristics on Grandchamp's French text)
# --------------------------------------------------------------------------

# Ordered: the first rule that matches gives the act's primary type.
ACT_TYPES = [
    ("mariage", r"promesse de mariage|mariage"),
    ("testament", r"testament|codicille"),
    ("deliberation", r"assembl[ée]e|d[ée]lib[ée]ration|r[ée]union de la nation|les experts"),
    ("affretement", r"nolis|affr[èe]te|fr[èe]te? (?:sa|la|une) (?:barque|navire|saetia|polacre)"),
    ("protestation", r"protest|(?-i:somme\s+(?:[A-Z][a-z]|M\W))|sommation"),
    ("procuration", r"procuration"),
    ("caution", r"caution"),
    ("reconnaissance_dette", r"r[ée]conna(?:î|i)t\s+devoir|reconnaissent\s+devoir|s'engage\s+à\s+(?:payer|rembourser)|s'oblige"),
    ("quittance", r"r[ée]conna(?:î|i)t\s+avoir\s+re[çc]u|reconnaissent\s+avoir\s+re[çc]u|quittance|d[ée]charge"),
    ("engagement", r"s'engage|promet|promesse|s'oblige"),
    ("vente_cession", r"\bvend|\bvente\b|\bc[èe]de\b|\bach[eè]te"),
    ("societe", r"soci[ée]t[ée]|association|compagnie"),
    ("accord", r"accord|convention|transaction"),
    ("decision", r"d[ée]cision|sentence|jugement"),
    ("declaration", r"d[ée]clar|attestation|certifi"),
    ("enregistrement", r"enregistrement|copie"),
]
ACT_TYPE_LABELS = {
    "mariage": "Promesse de mariage", "testament": "Testament",
    "deliberation": "Délibération / assemblée de la nation", "affretement": "Affrètement / nolis",
    "protestation": "Protestation / sommation", "procuration": "Procuration",
    "caution": "Caution", "reconnaissance_dette": "Reconnaissance de dette",
    "quittance": "Quittance / reçu", "engagement": "Engagement / promesse", "vente_cession": "Vente / cession",
    "societe": "Société / association", "accord": "Accord / convention",
    "decision": "Décision consulaire", "declaration": "Déclaration / attestation",
    "enregistrement": "Enregistrement / copie d'acte", "autre": "Autre",
}

CURRENCIES = {
    r"[ée]cus?": "écu", r"onces?|onc-es": "once", r"ducats?": "ducat", r"aspres?": "aspre",
    r"taris?": "tari", r"piastres?": "piastre", r"livres?": "livre", r"sols?|soubz": "sol",
    r"r[ée]aux|r[ée]als?": "réal", r"carlins?": "carlin", r"sequins?": "sequin",
    r"doubles?|doublons?": "doublon", r"sultanins?": "sultanin", r"florins?": "florin",
}
AMOUNT_RE = re.compile(
    r"(?<![\w/])(?P<num>\d{1,3}(?:[.\s]\d{3})*|\d+)"
    r"(?P<frac>\s*(?:et\s+(?:demi|\d/\d|[½¼¾])|[½¼¾]|\d/\d))?\s+"
    r"(?P<cur>" + "|".join(CURRENCIES) + r")\b"
    r"(?P<qual>(?:\s+(?:d'or|d'argent|de\s+Sicile|de\s+Naples|d'Espagne|coin\s+d'Espagne|sol|pistolets?|"
    r"de\s+France|de\s+Marseille|de\s+Malte|de\s+Venise|de\s+\d+\s+aspres|,\s*coin\s+d'Espagne))*)",
    re.I,
)


def classify(summary):
    s = summary
    for key, pat in ACT_TYPES:
        if re.search(pat, s, re.I):
            return key
    return "autre"


def extract_amounts(summary):
    out = []
    for m in AMOUNT_RE.finditer(summary):
        num = int(re.sub(r"[.\s]", "", m.group("num")))
        frac_raw = (m.group("frac") or "").strip()
        frac = 0.0
        if frac_raw:
            if "demi" in frac_raw or "½" in frac_raw:
                frac = 0.5
            elif "¼" in frac_raw:
                frac = 0.25
            elif "¾" in frac_raw:
                frac = 0.75
            else:
                fm = re.search(r"(\d)/(\d)", frac_raw)
                if fm and int(fm.group(2)):
                    frac = int(fm.group(1)) / int(fm.group(2))
        cur = next(v for k, v in CURRENCIES.items() if re.fullmatch(k, m.group("cur"), re.I))
        out.append({"amount": num + frac, "currency": cur,
                    "qualifier": re.sub(r"\s+", " ", m.group("qual") or "").strip(" ,") or None,
                    "raw": m.group(0).strip()})
    return out


def enrich(act):
    s = act["summary"]
    lang = None
    lm = re.search(r"\bEn (italien|latin|espagnol|provençal|français|turc|arabe)\b", s, re.I)
    if lm:
        lang = lm.group(1).lower()
    act["language"] = lang
    act["act_type"] = classify(s)
    act["concerns_ransom"] = bool(re.search(r"rach[ae]t|rachep|racheter|esclave", s, re.I))
    act["struck_out"] = bool(re.search(r"biff[ée]", s, re.I))
    act["illegible"] = bool(re.search(r"illisible", s, re.I))
    act["unfinished"] = bool(re.search(r"inachev[ée]|pas [ée]t[ée] achev[ée]|n'a pas [ée]t[ée] achev", s, re.I))
    act["damaged"] = bool(re.search(r"effac[ée]|d[ée]chir[ée]|rong[ée]|en partie|manque", s, re.I))
    act["mentions_extenso"] = "extenso" in s and not re.search(r"pas été reproduit\s+in[\s-]*extenso", s)
    refs = (re.findall(r"extenso[^()]{0,80}?\bp\.\s*([0-9][0-9il]{1,2})\b", s)
            or re.findall(r"extenso[^()]{0,40}?\b([0-9]{3})\)", s))   # OCR scrambled word order
    act["extenso_pages"] = [int(r.replace("i", "1").replace("l", "1")) for r in refs]
    act["amounts"] = extract_amounts(s)
    if act["undated"]:
        act["date_iso"] = None
    elif act["month"] and act["day"]:
        act["date_iso"] = f"{act['year']:04d}-{act['month']:02d}-{act['day']:02d}"
    elif act["month"]:
        act["date_iso"] = f"{act['year']:04d}-{act['month']:02d}"
    else:
        act["date_iso"] = f"{act['year']:04d}"
    return act


# --------------------------------------------------------------------------
# Parts II and III: documents copied in extenso
# --------------------------------------------------------------------------

ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
         (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n):
    out = ""
    for v, r in ROMAN:
        while n >= v:
            out += r
            n -= v
    return out


TOC_START_RE = re.compile(r"^\s*[.i]?\s*(?P<roman>[IVXLÎ]{1,8})\s*[.,]?\s*[—–-]\s*(?P<title>\S.*)$")
TOC_PAGE_RE = re.compile(r"^(?P<body>.*?)[\s.]{3,}(?P<page>[0-9liHO]{2,3})\s*$")
DATE_IN_TITLE_RE = re.compile(
    r"(?:(?P<d>[0-9]{1,2}|[l1I\]][^\s\d]{1,4})(?:er|«|\"|°|\*+)?\s*(?P<m>[a-zéû]+)\s+)?(?P<y>1[56]\d\d)")


def norm_toc_page(tok):
    return int(tok.replace("l", "1").replace("i", "1").replace("H", "11").replace("O", "0"))


def parse_iso_from_title(title):
    """Date of the act: the date in brackets at the end of the title, else the last date."""
    pm = re.search(r"\(([^()]*\d[^()]*)\)\s*['.]*\s*$", title)
    if pm:
        title = pm.group(1)
        if "et" in title.split():   # "(22 août et 1er septembre 1593)" -> first date
            title = title.split(" et ")[0] + " " + title.split()[-1]
    title = re.sub(r"\b1\s([56]\d\d)\b", r"1\1", title)   # "1 596" -> "1596"
    found = None
    for m in DATE_IN_TITLE_RE.finditer(title):
        found = m
    if not found:
        return None
    y = int(found.group("y"))
    mon = norm_month(found.group("m")) if found.group("m") else None
    day = None
    if found.group("d"):
        d = found.group("d")
        day = norm_day(d) if d.isdigit() else 1   # "1er" in its OCR variants
    if mon and day:
        return f"{y:04d}-{mon:02d}-{day:02d}"
    if mon:
        return f"{y:04d}-{mon:02d}"
    return f"{y:04d}"


def all_title_dates(title):
    """Every full date mentioned in a title, as ISO strings."""
    t = re.sub(r"\b1\s([56]\d\d)\b", r"1\1", title)
    t = re.sub(r"\bet\s+(?=\S+\s+[a-zéû]+\s+1[56]\d\d)", " ", t)
    out = []
    # "22 août et 1er septembre 1593": give the first date the year of the second
    m2 = re.search(r"(\d{1,2})\s+([a-zéû]+)\s+et\s+\S+\s+[a-zéû]+\s+(1[56]\d\d)", title)
    if m2 and norm_month(m2.group(2)):
        out.append(f"{int(m2.group(3)):04d}-{norm_month(m2.group(2)):02d}-{int(m2.group(1)):02d}")
    for m in DATE_IN_TITLE_RE.finditer(t):
        if m.group("m") and m.group("d") and norm_month(m.group("m")):
            d = m.group("d")
            day = norm_day(d) if d.isdigit() else 1
            if day:
                out.append(f"{int(m.group('y')):04d}-{norm_month(m.group('m')):02d}-{day:02d}")
    return out


def parse_toc(pdf, vol, part):
    """Parse the 'Table des matières' entries for one part."""
    off = vol["page_offset"]
    first, last = part["toc_pages"]
    lines = []
    for printed in range(first, last + 1):
        lines += printed_text(vol, printed).split("\n")
    # Keep only the slice belonging to this part.
    start_pat = re.compile(rf"^\s*{part['number']}\.\s*[—–-]\s*Actes")
    in_part, entries, cur = False, [], None
    for line in lines:
        if start_pat.match(line):
            in_part = True
            continue
        if not in_part:
            continue
        if re.match(r"^\s*[IVX]+\.\s*[—–-]\s*Actes", line):   # next part
            break
        if not line.strip() or PAGE_HDR_RE.match(line) or line.strip() == "Pages":
            continue
        sm = TOC_START_RE.match(line)
        if sm and cur is None:
            cur = {"roman_ocr": sm.group("roman"), "text": sm.group("title")}
        elif cur is not None:
            cur["text"] += " " + line.strip()
        else:
            continue
        pm = TOC_PAGE_RE.match(cur["text"])
        if pm:
            title = join_text([pm.group("body")]).rstrip(" .")
            cur["title"] = re.sub(r"(\w)- (\w)", r"\1\2", title)   # rejoin "novem- bre"
            cur["page"] = norm_toc_page(pm.group("page"))
            entries.append(cur)
            cur = None
    for i, e in enumerate(entries, 1):
        e["number"] = i
        e["roman"] = to_roman(i)
        if e["roman_ocr"].replace("Î", "I") != e["roman"]:
            print(f"  ? part {part['number']} entry {i}: TOC numeral read as {e['roman_ocr']!r}",
                  file=sys.stderr)
        fix = vol.get("toc_page_fixes", {}).get(f"{part['number']}:{i}")
        if fix:
            e["page"] = fix
        e["date_iso"] = parse_iso_from_title(e["title"])
        e["undated"] = bool(re.search(r"\(s\.\s?d\.\)", e["title"]))
    # Page numbers must not decrease; report the ones that do (OCR errors).
    for a, b in zip(entries, entries[1:]):
        if b["page"] < a["page"]:
            print(f"  ! part {part['number']}: TOC page {b['page']} after {a['page']} ({b['title'][:40]})",
                  file=sys.stderr)
    return entries


HEADING_RE = re.compile(r"^\s{6,}(?P<r>[IVXLivxlnmuUjyÎ1]{1,9})\s*\.?(?:\s{3,}\S.*)?$")


def norm_roman_ocr(tok):
    """Undo OCR confusions in a small roman numeral: 'xn' -> XII, 'xxxvm' -> XXXVIII."""
    t = tok.replace("m", "iii").replace("n", "ii").replace("u", "ii").replace("U", "II")
    t = re.sub(r"[lLjyÎ1]", "I", t)
    return t.upper()


def heading_line_index(lines, roman):
    for i, l in enumerate(lines):
        m = HEADING_RE.match(l)
        if m and norm_roman_ocr(m.group("r")) == roman:
            return i
    return None


def document_texts(pdf, vol, part, entries):
    off = vol["page_offset"]
    last_page = part["text_pages"][1]
    cache = {}

    def page_lines(p):
        if p not in cache:
            cache[p] = [l for l in printed_text(vol, p).split("\n")
                        if not PAGE_HDR_RE.match(l)]
        return cache[p]

    for i, e in enumerate(entries):
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        end_page = nxt["page"] if nxt else last_page
        chunks = []
        for p in range(e["page"], end_page + 1):
            lines = page_lines(p)
            lo, hi = 0, len(lines)
            if p == e["page"]:
                h = heading_line_index(lines, e["roman"])
                if h is None and not (i and entries[i - 1]["page"] == p):
                    h = -1   # first document on the page, printed without a numeral
                if h is None:
                    print(f"  ? document {e['roman']} heading not found on page {p}", file=sys.stderr)
                lo = h + 1 if h is not None else 0
                if h is not None and h >= 0:
                    # keep the date printed on the heading line, if any
                    rest = HEADING_RE.match(lines[h])
                    tail = lines[h][rest.end("r"):].strip(" .")
                    if tail:
                        lines = lines[:h] + [" " * 40 + tail] + lines[h + 1:]
                        lo = h
            if nxt and p == nxt["page"]:
                h = heading_line_index(lines, nxt["roman"])
                if h is not None:
                    hi = h
                elif p != e["page"]:
                    hi = 0   # next document starts on this page; heading not found, skip
            chunks.append("\n".join(lines[lo:hi]))
        text = "\n".join(chunks)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        e["page_end"] = end_page if (nxt is None or nxt["page"] > e["page"]) else e["page"]
        if nxt and nxt["page"] > e["page"] and heading_line_index(page_lines(nxt["page"]), nxt["roman"]) in (None, 0):
            e["page_end"] = nxt["page"] - 1
        e["text"] = text
    return entries


def link_documents(act, docs):
    """Match an act whose summary says "Voir copie in extenso p. N" to its document(s).

    The printed page references are sometimes wrong (or misread), so a document
    with the same date as the act is preferred, using the page to break ties;
    otherwise the document printed on the referenced page is used."""
    if not act["mentions_extenso"]:
        return []
    links = []
    same_date = [d for d in docs if act["date_iso"] and act["date_iso"] in d[5]]
    pages = act["extenso_pages"]
    if len(same_date) == 1:
        d = same_date[0]
        in_range = any(d[0] <= p <= (d[1] or d[0]) for p in pages)
        links.append((d[2], "date+page" if in_range else "date"))
    elif same_date:
        # Several documents share the date: score each on title words, plus a
        # bonus when the cited page falls inside it (page numbers can be wrong).
        words = _words(act["summary"])
        def score(d):
            in_range = any(d[0] <= p <= (d[1] or d[0]) for p in pages)
            return _sim(words, _words(d[4])) + (1.0 if in_range else 0.0), in_range
        best = max(same_date, key=lambda d: score(d)[0])
        sc, in_range = score(best)
        if sc > 0:
            links.append((best[2], "date+page" if in_range else "date+title"))
    if not links:
        for p in pages:
            cands = [d for d in docs if d[0] <= p <= (d[1] or d[0])]
            if cands:
                words = _words(act["summary"])
                best = max(cands, key=lambda d: (d[0] == p, _sim(words, _words(d[4]))))   # refs cite start pages
                links.append((best[2], "page"))
    if not links:
        # No usable page: compare the summary's words with the documents' titles.
        cands = same_date or [d for d in docs if not d[3]] if act["undated"] else same_date
        best, score = None, 0.0
        words = _words(act["summary"])
        for d in cands:
            sc = _sim(words, _words(d[4]))
            if sc > score:
                best, score = d, sc
        if best and score >= 0.2:
            links.append((best[2], "date+title" if same_date else "title"))
    return list(dict.fromkeys(links))


def _sim(a, b):
    """Cosine similarity of two word sets."""
    return len(a & b) / (max(1, len(a)) * max(1, len(b))) ** 0.5


def _words(text):
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z]{4,}", t)}


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE volumes (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    author        TEXT,
    published     TEXT,
    period_start  TEXT,
    period_end    TEXT,
    source_url    TEXT,
    file          TEXT,
    page_offset   INTEGER            -- pdf_page = printed page + page_offset
);

CREATE TABLE registers (
    id          INTEGER PRIMARY KEY,
    volume_id   INTEGER NOT NULL REFERENCES volumes(id),
    number      TEXT NOT NULL,       -- chancellery register number (I, II, ...)
    label       TEXT,
    date_start  TEXT,
    date_end    TEXT,
    UNIQUE (volume_id, number)
);

CREATE TABLE documents (               -- acts copied in extenso (Parts II, III, ...)
    id           INTEGER PRIMARY KEY,
    volume_id    INTEGER NOT NULL REFERENCES volumes(id),
    part         TEXT NOT NULL,        -- part of the book (II, III)
    part_label   TEXT,
    number       INTEGER NOT NULL,     -- document number within the part
    roman        TEXT NOT NULL,        -- as printed (I, II, ...)
    title        TEXT NOT NULL,        -- editor's title from the table of contents
    date_iso     TEXT,                 -- YYYY-MM-DD, YYYY-MM or YYYY (date of the act)
    undated      INTEGER NOT NULL DEFAULT 0,
    page_start   INTEGER NOT NULL,     -- printed page
    page_end     INTEGER,
    pdf_page     INTEGER NOT NULL,
    text         TEXT,                 -- OCR transcription (16th-c. French / Italian)
    UNIQUE (volume_id, part, number)
);

CREATE TABLE acts (                    -- one row per act analysed in Part I
    id               INTEGER PRIMARY KEY,
    volume_id        INTEGER NOT NULL REFERENCES volumes(id),
    ref              TEXT NOT NULL UNIQUE,   -- e.g. "1-II-0123" (volume-register-sequence)
    register         TEXT NOT NULL,
    seq              INTEGER NOT NULL,       -- order within the volume, as printed
    date_iso         TEXT,                   -- YYYY-MM-DD, YYYY-MM or YYYY; NULL when undated
    year             INTEGER,
    month            INTEGER,
    day              INTEGER,
    undated          INTEGER NOT NULL DEFAULT 0,  -- "s. d." in the book
    date_supplied    INTEGER NOT NULL DEFAULT 0,  -- date given in [brackets] by the editor
    date_raw         TEXT,                   -- date column exactly as OCR read it
    summary          TEXT NOT NULL,          -- Grandchamp's analysis (French)
    language         TEXT,                   -- original language when noted ("italien", "latin"...)
    act_type         TEXT NOT NULL,          -- heuristic, see act_types
    concerns_ransom  INTEGER NOT NULL DEFAULT 0,
    struck_out       INTEGER NOT NULL DEFAULT 0,  -- "biffé"
    illegible        INTEGER NOT NULL DEFAULT 0,
    unfinished       INTEGER NOT NULL DEFAULT 0,
    damaged          INTEGER NOT NULL DEFAULT 0,  -- effacé, rongé, déchiré, en partie...
    consul           TEXT,                   -- officials in office per the book's headings
    vice_consul      TEXT,
    chancelier       TEXT,
    page             INTEGER NOT NULL,       -- printed page
    pdf_page         INTEGER NOT NULL,
    -- transaction details (rule-based draft, corrected by hand: see annotations/)
    month_day              TEXT,      -- "25 avril" / "s. d."
    transaction_type       TEXT,      -- Redemption, Debt, Payment / receipt, Sale / transfer, Power of attorney...
    party1_name            TEXT,      -- the party making the act (debtor, grantor, recipient...); several = "; "
    party1_origins         TEXT,      -- origin/nationality as given ("sicilien", "de Trapani"), aligned with names
    party1_role            TEXT,      -- role in the transaction, then status/occupation
    party2_name            TEXT,      -- the counterparty (creditor, attorney, payer...)
    party2_origins         TEXT,
    party2_role            TEXT,
    other_parties_names    TEXT,      -- others mentioned: captive, captor paid, guarantor...
    other_parties_origins  TEXT,
    other_parties_role     TEXT,
    matter                 TEXT,      -- what the transaction is about
    price                  REAL,      -- principal sum
    currency               TEXT,      -- currency of the price, as qualified in the text
    other_equivalent       TEXT,      -- further sums, rates of exchange, goods given in exchange
    keywords               TEXT,      -- "; "-separated
    decision               TEXT,      -- outcome of decisions, deliberations, disputes
    notes                  TEXT,      -- terms, later annotations, condition of the act, language
    extraction             TEXT       -- 'auto' (rules only) or 'reviewed' (checked by hand)
);
CREATE INDEX acts_date ON acts(date_iso);
CREATE INDEX acts_type ON acts(act_type);

CREATE TABLE act_documents (           -- "Voir copie in extenso": act -> full copy in Part II/III
    act_id       INTEGER NOT NULL REFERENCES acts(id),
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    method       TEXT NOT NULL,        -- date+page, date, page, date+title, title: how it was matched
    PRIMARY KEY (act_id, document_id)
);

CREATE TABLE act_types (
    code   TEXT PRIMARY KEY,
    label  TEXT NOT NULL
);

CREATE TABLE act_amounts (             -- sums of money mentioned in an act's summary
    id         INTEGER PRIMARY KEY,
    act_id     INTEGER NOT NULL REFERENCES acts(id),
    amount     REAL NOT NULL,
    currency   TEXT NOT NULL,          -- normalised: écu, once, ducat, aspre, tari, ...
    qualifier  TEXT,                   -- "d'or d'Espagne", "sol", "de Sicile"...
    raw        TEXT NOT NULL
);
CREATE INDEX act_amounts_act ON act_amounts(act_id);

CREATE TABLE officials (               -- bold headings naming consuls / chancellors
    id           INTEGER PRIMARY KEY,
    volume_id    INTEGER NOT NULL REFERENCES volumes(id),
    name         TEXT NOT NULL,        -- spelling as printed
    role         TEXT NOT NULL,        -- consul, vice_consul, chancelier
    register     TEXT,
    first_act_id INTEGER REFERENCES acts(id),
    page         INTEGER NOT NULL,
    pdf_page     INTEGER NOT NULL
);

CREATE TABLE footnotes (
    id         INTEGER PRIMARY KEY,
    volume_id  INTEGER NOT NULL REFERENCES volumes(id),
    page       INTEGER NOT NULL,
    pdf_page   INTEGER NOT NULL,
    marker     INTEGER,
    text       TEXT NOT NULL
);

CREATE VIRTUAL TABLE acts_fts USING fts5(
    summary, content='acts', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title, text, content='documents', content_rowid='id', tokenize='unicode61 remove_diacritics 2');

CREATE VIEW v_transactions AS
SELECT a.ref, a.year, a.month, a.day, a.month_day, a.transaction_type,
       a.party1_name, a.party1_origins, a.party1_role,
       a.party2_name, a.party2_origins, a.party2_role,
       a.other_parties_names, a.other_parties_origins, a.other_parties_role,
       a.matter, a.price, a.currency, a.other_equivalent, a.keywords, a.decision, a.notes,
       a.summary, a.volume_id AS volume, a.register, a.page, a.pdf_page, a.extraction
FROM acts a ORDER BY a.volume_id, a.seq, a.ref;

CREATE VIEW v_acts AS
SELECT a.ref, a.date_iso, a.date_raw, t.label AS type, a.language, a.summary,
       a.concerns_ransom, a.struck_out, a.illegible, a.unfinished, a.damaged,
       a.consul, a.vice_consul, a.chancelier,
       (SELECT group_concat(d.part || '-' || d.roman, '; ') FROM act_documents ad
          JOIN documents d ON d.id = ad.document_id WHERE ad.act_id = a.id) AS full_text_documents,
       v.id AS volume, a.register, a.page, a.pdf_page,
       (SELECT group_concat(raw, '; ') FROM act_amounts m WHERE m.act_id = a.id) AS amounts
FROM acts a
JOIN volumes v ON v.id = a.volume_id
JOIN act_types t ON t.code = a.act_type
ORDER BY a.volume_id, a.seq, a.ref;
"""


def build(config_path, out_path):
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    db = sqlite3.connect(out_path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO act_types VALUES (?, ?)", ACT_TYPE_LABELS.items())

    for vol in cfg["volumes"]:
        pdf = ROOT / vol["file"]
        if not pdf.exists():
            print(f"skipping volume {vol['id']}: {pdf} not found", file=sys.stderr)
            continue
        if vol.get("text_source") and not (ROOT / vol["text_source"] / "index.json").exists():
            print(f"skipping volume {vol['id']}: OCR cache {vol['text_source']} not built yet "
                  f"(run scripts/ocr_volume.py {vol['id']})", file=sys.stderr)
            continue
        print(f"Volume {vol['id']}: {vol['title']}", file=sys.stderr)
        vid = vol["id"]
        db.execute("INSERT INTO volumes VALUES (?,?,?,?,?,?,?,?,?)",
                   (vid, vol["title"], vol.get("author"), vol.get("published"),
                    vol.get("period_start"), vol.get("period_end"), vol.get("source_url"),
                    vol["file"], vol.get("page_offset")))
        for r in vol["registers"]:
            db.execute("INSERT INTO registers (volume_id, number, label, date_start, date_end) "
                       "VALUES (?,?,?,?,?)",
                       (vid, r["number"], r["label"], r["date_start"], r["date_end"]))

        # Documents first, so acts can point at them.
        doc_by_page = []
        for part in vol.get("parts", []):
            entries = parse_toc(pdf, vol, part)
            document_texts(pdf, vol, part, entries)
            for e in entries:
                cur = db.execute(
                    "INSERT INTO documents (volume_id, part, part_label, number, roman, title, "
                    "date_iso, undated, page_start, page_end, pdf_page, text) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (vid, part["number"], part["label"], e["number"], e["roman"], e["title"],
                     e["date_iso"], int(e["undated"]), e["page"], e["page_end"],
                     e["page"] + vol["page_offset"], e["text"]))
                doc_by_page.append((e["page"], e["page_end"], cur.lastrowid, e["date_iso"], e["title"],
                                    all_title_dates(e["title"])))
            print(f"  part {part['number']}: {len(entries)} documents", file=sys.stderr)

        acts_file = ROOT / "text" / f"vol{vid}" / "acts.txt"
        if vol.get("pages_dir") and acts_file.exists():
            # the organised text file is the source (scripts/organize_text.py writes it)
            import organize_text
            acts, officials, footnotes = organize_text.read_acts(acts_file)
            idx = json.loads((ROOT / vol["pages_dir"] / "index.json").read_text())
            for o in officials:
                nxt = acts[min(o["after_act_seq"], len(acts) - 1)]
                o["register"] = nxt["register"]
                o["pdf_page"] = idx.get(f"p{o['page']:03d}", {}).get("pdf_page")
            for f in footnotes:
                f["pdf_page"] = idx.get(f"p{f['page']:03d}", {}).get("pdf_page")
        else:
            acts, officials, footnotes = parse_analysis(pdf, vol)
        ann_path = ROOT / "annotations" / f"vol{vid}.json"
        ann = json.loads(ann_path.read_text(encoding="utf-8")) if ann_path.exists() else {}
        overrides = ann.get("acts", {})
        reviewed_upto = ann.get("reviewed_through_seq", 0)
        seq_by_index = {}
        prev_tx = None
        # Refs come from the printed order; a hand "split_at" turns one OCR-merged act into
        # several, the extra pieces getting suffixed refs (…0203b, …0203c) so others are stable.
        expanded = []
        for i, a in enumerate(acts, 1):
            a["ref"] = f"{vid}-{a['register']}-{i:04d}"
            a["orig_seq"] = i
            marks = overrides.get(a["ref"], {}).get("split_at")
            if not marks:
                expanded.append(a)
                continue
            text, pieces = a["summary"], []
            for mark in marks:
                k = text.find(mark)
                if k <= 0:
                    raise SystemExit(f"annotations/vol{vid}.json: split_at {mark!r} not found in {a['ref']}")
                pieces.append(text[:k].strip())
                text = text[k:]
            pieces.append(text.strip())
            for n, piece in enumerate(pieces):
                expanded.append(dict(a, summary=piece, ref=a["ref"] + ("" if n == 0 else "bcdefgh"[n - 1]),
                                     date_raw=a["date_raw"] if n == 0 else "(date lost in the scan)",
                                     day_guess=a.get("day_guess") or n > 0, piece=n))
        for seq, a in enumerate(expanded, 1):
            i = a["orig_seq"]
            enrich(a)
            # "Même acte..." / "Acte semblable...": same kind of act as the previous one
            if seq > 1 and re.match(r"(?:En \w+\. — )?(?:M[êe]me acte|Acte sem\w*blable)", a["summary"]):
                a["act_type"] = expanded[seq - 2]["act_type"]
            ref = a["ref"]
            if overrides.get(ref, {}).get("drop"):
                continue             # OCR debris, not an act (the ref is left unused)
            if vol.get("text_source"):
                a["summary"] = re.sub(r"(?<=\s)[|¦©®ÈÌ](?=\s)|\s+[|¦]\s*$", "", a["summary"])
                a["summary"] = re.sub(r"\s{2,}", " ", a["summary"]).strip()
            # hand-corrected date (OCR misreadings): "date" = YYYY-MM-DD / YYYY-MM / null (undated)
            if "date" in overrides.get(ref, {}):
                d = overrides[ref]["date"]
                parts = [int(x) for x in d.split("-")] if d else []
                a["undated"] = not parts
                a["year"] = parts[0] if parts else a["year"]
                a["month"] = parts[1] if len(parts) > 1 else None
                a["day"] = parts[2] if len(parts) > 2 else None
                a["day_guess"] = False
                a["date_iso"] = d
            links = link_documents(a, doc_by_page)
            tx = extract_parties.to_columns(a, extract_parties.extract(a))
            # "Le même" / "au même": the party of the previous act
            if prev_tx:
                if tx["party1_name"] and re.fullmatch(r"(?:Le|La|Les) m[êe]mes?|\(same as previous act\)", tx["party1_name"]):
                    for f in ("name", "origins"):
                        tx[f"party1_{f}"] = prev_tx[f"party1_{f}"]
                    tx["party1_role"] = tx["party1_role"] or prev_tx["party1_role"]
                    tx["notes"] = ((tx["notes"] or "") + " Party 1 is the same as in the previous act.").strip()
                if re.search(r"\b(?:au|du|le) même\b(?!\s+(?:lieu|ville|jour|objet|sujet))", a["summary"]) and not tx["party2_name"]:
                    for f in ("name", "origins", "role"):
                        tx[f"party2_{f}"] = prev_tx[f"party2_{f}"]
                if re.match(r"(?:En \w+\. — )?(?:M[êe]me acte|Acte sem\w*blable)", a["summary"]):
                    for side, nm in (("party1", "name"), ("party2", "name"), ("other_parties", "names")):
                        if not tx[f"{side}_{nm}"] or tx[f"{side}_{nm}"].startswith("("):
                            for f in (nm, "origins", "role"):
                                tx[f"{side}_{f}"] = prev_tx[f"{side}_{f}"]
                    tx["transaction_type"] = prev_tx["transaction_type"]
            if a.get("day_guess"):
                why = ("date not legible in the scan; taken from the previous act"
                       if a["date_raw"].startswith("(") else f"day reconstructed from a damaged OCR reading ({a['date_raw']})")
                tx["notes"] = (why[0].upper() + why[1:] + ". " + (tx["notes"] or "")).strip()
            if a.get("date_supplied"):
                tx["notes"] = ("Undated in the register (s. d.); date supplied by the editor. " + (tx["notes"] or "")).strip()
            if a.get("date_note"):
                tx["notes"] = (a["date_note"].rstrip(".") + ". " + (tx["notes"] or "")).strip()
            for k, v in overrides.get(ref, {}).items():
                if k in ("date", "drop", "split_at"):
                    continue
                if k not in tx:
                    raise SystemExit(f"annotations/vol{vid}.json: unknown field {k!r} for {ref}")
                tx[k] = v
            # A hand-written note replaces the generated one; keep the register folio.
            fm = re.match(r"^\W*(?:E[nt]i?\s*italien\W*)?\(\s*(\d+)\s*\)", a["summary"].strip())
            if fm and "Folio" not in (tx["notes"] or ""):
                tx["notes"] = (f"Folio {fm.group(1)} of the register. " + (tx["notes"] or "")).strip()
            # ";" separates people; when a role/origin list does not line up one-to-one
            # with the names, its ";" were separating facets of one description.
            for nm, fields in (("party1_name", ("party1_origins", "party1_role")),
                               ("party2_name", ("party2_origins", "party2_role")),
                               ("other_parties_names", ("other_parties_origins", "other_parties_role"))):
                n = len(tx[nm].split(";")) if tx[nm] else 0
                for f in fields:
                    v = tx[f]
                    if v is not None and not v.strip(" ;,"):
                        tx[f] = None
                    elif v and len(v.split(";")) != n:
                        tx[f] = re.sub(r"\s*;\s*", ", ", v).strip(" ,")
            prev_tx = dict(tx)
            tx["extraction"] = "reviewed" if (i <= reviewed_upto or ref in overrides) else "auto"
            cur = db.execute(
                "INSERT INTO acts (volume_id, ref, register, seq, date_iso, year, month, day, "
                "undated, date_supplied, date_raw, summary, language, act_type, concerns_ransom, "
                "struck_out, illegible, unfinished, damaged, consul, vice_consul, chancelier, "
                "page, pdf_page) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vid, ref, a["register"], i, a["date_iso"], a["year"],
                 None if a["undated"] else a["month"], a["day"], int(a["undated"]),
                 int(a["date_supplied"]), a["date_raw"], a["summary"], a["language"], a["act_type"],
                 int(a["concerns_ransom"]), int(a["struck_out"]), int(a["illegible"]),
                 int(a["unfinished"]), int(a["damaged"]), a["consul"], a["vice_consul"],
                 a["chancelier"], a["page"], a["pdf_page"]))
            act_id = cur.lastrowid
            db.execute("UPDATE acts SET " + ", ".join(f"{k} = ?" for k in tx) + " WHERE id = ?",
                       [*tx.values(), act_id])
            for doc_id, method in links:
                db.execute("INSERT OR IGNORE INTO act_documents VALUES (?,?,?)", (act_id, doc_id, method))
            if a["mentions_extenso"] and not links:
                print(f"  ? {vid}-{a['register']}-{i:04d}: 'in extenso' copy not matched", file=sys.stderr)
            if not a.get("piece"):
                seq_by_index[i - 1] = act_id
            for m in a["amounts"]:
                db.execute("INSERT INTO act_amounts (act_id, amount, currency, qualifier, raw) "
                           "VALUES (?,?,?,?,?)", (act_id, m["amount"], m["currency"],
                                                  m["qualifier"], m["raw"]))
        unknown = set(overrides) - {a["ref"] for a in expanded}
        if unknown:
            print(f"  ! annotations refer to unknown acts: {sorted(unknown)[:5]}", file=sys.stderr)
        for o in officials:
            db.execute("INSERT INTO officials (volume_id, name, role, register, first_act_id, page, "
                       "pdf_page) VALUES (?,?,?,?,?,?,?)",
                       (vid, o["name"], o["role"], o["register"],
                        seq_by_index.get(o["after_act_seq"]), o["page"], o["pdf_page"]))
        for f in footnotes:
            db.execute("INSERT INTO footnotes (volume_id, page, pdf_page, marker, text) "
                       "VALUES (?,?,?,?,?)", (vid, f["page"], f["pdf_page"], f["marker"], f["text"]))
        print(f"  part I: {len(acts)} acts, {len(officials)} officials, {len(footnotes)} footnotes",
              file=sys.stderr)

    db.execute("INSERT INTO acts_fts(acts_fts) VALUES ('rebuild')")
    db.execute("INSERT INTO documents_fts(documents_fts) VALUES ('rebuild')")
    db.commit()
    db.execute("VACUUM")
    db.close()


def export_csv(db_path, out_dir):
    import csv
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True)
    db = sqlite3.connect(db_path)
    for name, sql in [("acts", "SELECT * FROM v_acts"),
                      ("transactions", "SELECT * FROM v_transactions"),
                      ("act_amounts", "SELECT a.ref, m.amount, m.currency, m.qualifier, m.raw "
                                      "FROM act_amounts m JOIN acts a ON a.id = m.act_id ORDER BY a.seq"),
                      ("documents", "SELECT volume_id, part, roman, title, date_iso, page_start, "
                                    "page_end, pdf_page FROM documents ORDER BY volume_id, part, number"),
                      ("officials", "SELECT o.volume_id, o.name, o.role, o.register, a.ref AS first_act, "
                                    "a.date_iso AS from_date, o.page FROM officials o "
                                    "LEFT JOIN acts a ON a.id = o.first_act_id ORDER BY o.id")]:
        cur = db.execute(sql)
        # utf-8-sig so Excel opens accented text correctly
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur)
    db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "volumes.json"))
    ap.add_argument("--out", default=str(ROOT / "catalogue.sqlite"))
    ap.add_argument("--csv-dir", default=str(ROOT / "csv"))
    args = ap.parse_args()
    build(args.config, args.out)
    export_csv(args.out, args.csv_dir)
    print(f"wrote {args.out} and CSV files in {args.csv_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
