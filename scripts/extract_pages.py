#!/usr/bin/env python3
"""Write one text file per printed page of a volume from its scan's text layer.

The HathiTrust scans come split into several PDFs (volumes.json -> "scans"). Each PDF page
is converted with `pdftotext -layout`, its printed page number is read from the running
header, and the text is saved as text/vol<N>/pages/p<NNN>.txt (front matter and plates,
which carry no arabic number, as fm<NN>.txt). pages/index.json maps every file to its PDF
part and page.

The Google OCR puts spaces before punctuation ("Natale , de Sicano") and drops the dash
after the language note ("En italien .  Antonio"); both are normalised here. The layout
(margin dates at the left, text indented) is kept, because the act parser relies on it.

Usage: python3 scripts/extract_pages.py 2
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

NUM_LINE = re.compile(r"^[\s\-–—~.:|_]*(\d{1,3})[\s\-–—~.:|_]*$")
YEAR_LINE = re.compile(r"^\s*1[56]\d\d\s*$")


def pdf_pages(pdf):
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1))


def layout_text(pdf, page):
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout.replace("\f", "")


def header_number(text):
    """The printed page number: a bare number in the first lines (the running header),
    or at the foot of a page that opens a part."""
    lines = [l for l in text.split("\n") if l.strip()]
    for l in lines[:4] + lines[-2:]:
        if YEAR_LINE.match(l):
            continue
        m = NUM_LINE.match(l)
        if m and 0 < int(m.group(1)) < 800:
            return int(m.group(1))
    for l in lines[:2]:
        # running title and number on one line: "ANALYSE DES ACTES (1651-1660)     3",
        # "  275     TABLE DES NOMS CITÉS"
        m = (re.match(r"^\s*[A-ZÀ-Ý][A-ZÀ-Ýa-zà-ÿ ()\d.,'’\-–—]{6,}?\s{2,}(\d{1,3})\s*$", l)
             or re.match(r"^\s*(\d{1,3})\s{2,}[A-ZÀ-Ý][A-ZÀ-Ý ()\d.,'’\-–—]{6,}$", l))
        if m and re.search(r"[A-ZÀ-Ý]{4,}", l) and 0 < int(m.group(1)) < 800:
            return int(m.group(1))
    return None


def clean_second(text):
    """Tesseract text of the photocopy: blank the specks left of the margin and at line
    ends, then move the date column to column 0."""
    junk = re.compile(r"^(\s*)((?:[^\sA-Za-zÀ-ÿ0-9]{1,3}|[A-Za-z]{1,2}[^\w\s]?|[A-Z][A-Za-z],?)(?=\s{3,}))")
    date = re.compile(r"^\s*\d{1,2}(?:er)?\s+(?:[—–-]|[a-zéû]{3,5}\.?)\s{2,}\S")
    out = []
    for l in text.split("\n"):
        l = l.rstrip()
        l = re.sub(r"(?:\s+(?:[|\\/.:;,'‘’`~_^°»«{}\[\]]+|[A-ZÈÉÌÎÏÀÂÄÆ€]|\d))+\s*$", "", l) if len(l.strip()) > 12 else l
        for _ in range(3):
            m = junk.match(l) or re.match(r"^(\s*)([ihlIj{}|_\-.;:'()\[\]]{1,3}(?: [ihlIj{}|_.;:'()]{1,2})?)(?=\s{3,})", l)
            if not m or re.fullmatch(r"\d{1,2}", m.group(2)):
                break
            l = m.group(1) + " " * len(m.group(2)) + l[m.end():]
        # a lone gutter digit in front of a continuation line (lower case): not a date
        l = re.sub(r"^(\s*)(\d|\d \d)(\s{4,})(?=[a-zà-ÿ(.«])", lambda mm: mm.group(1) + " " * len(mm.group(2)) + mm.group(3), l)
        l = re.sub(r"^(\s*)\d (?=\d{1,2}\s+[—–-]\s)", r"\1  ", l)       # "1 2  —" -> "2  —"
        if re.fullmatch(r"\s*[|\\/.:;,'‘’`~_^°»«{}A-Za-z\d]{1,3}\s*", l) and not re.fullmatch(r"\s*1[56]\d\d\s*", l):
            l = ""
        out.append(l)
    ind = [len(l) - len(l.lstrip()) for l in out if date.match(l)]
    years = [len(l) - len(l.lstrip()) for l in out if re.fullmatch(r"\s*1[56]\d\d\s*", l)]
    cut = min(ind) if ind else (min(years) if years else 0)
    return "\n".join(l[cut:] if len(l) - len(l.lstrip()) >= cut else l.lstrip() for l in out)


def tesseract_page(pdf, page):
    """OCR one page image with Tesseract, keeping the layout (scripts/ocr_volume.py)."""
    import tempfile
    import ocr_volume as V
    with tempfile.TemporaryDirectory() as tmp:
        img = V.render(pdf, page, tmp)
        img, _ = V.deskew(img)
        text = V.layout_text(V.ocr_words(img, tmp), img.width)
    return "\n".join(l for l in text.split("\n") if not re.fullmatch(r"\s*(?:Google|Digitized by.*|Original from.*)\s*", l))


MARGIN = re.compile(r"^(\s{0,4})((?:1er|\d{1,2}|s\. d\.|\?|\.{2,})(?:\s+[a-zéû]{3,9}\.?)?(?:\s+[—–-]{1,3})?|[—–-]{1,3}|\S{1,3}\s+[—–-])"
                    r"(?=\s{2,}(?:En \w+\. —|\(|[A-ZÉ«]))")


def transplant_dates(tess, google, lab):
    """Tesseract reads the text of a curved page well but misses some margin days (it
    renders them as « — »); the Google OCR of the same page has the dates right. Act starts
    are matched by their first words (in order) and take the Google margin date."""
    import difflib
    def key(rest):
        return re.sub(r"[^a-z0-9]", "", rest.lower().replace("en italien", ""))[:30]
    gl = [(m.group(2), key(l[m.end():])) for l in google.split("\n") for m in [MARGIN.match(l)] if m]
    tl = tess.split("\n")
    j, moved, missed = 0, 0, 0
    for i, l in enumerate(tl):
        m = MARGIN.match(l)
        if not m:
            continue
        k = key(l[m.end():])
        hit = None
        for jj in range(j, min(j + 4, len(gl))):
            if difflib.SequenceMatcher(None, k, gl[jj][1]).ratio() >= 0.6:
                hit = jj
                break
        if hit is None:
            missed += 1
            continue
        d = gl[hit][0]
        j = hit + 1
        if d != m.group(2).strip():
            tl[i] = m.group(1) + d + " " * max(4, 12 - len(m.group(1) + d)) + l[m.end():].lstrip()
            moved += 1
    if missed:
        print(f"  ? {lab}: {missed} act start(s) without a matching Google margin date", file=sys.stderr)
    return "\n".join(tl)


def reindent(text):
    """Tesseract layout of a page without margin dates starts its text at column 0, where
    the parser expects dates: move such pages' text to the usual indent."""
    date = re.compile(r"^\s{0,3}(?:\d{1,2}(?:er)?|1er|s\.\s?d\.|[—–-])\s*(?:[a-zéû]{2,6}\.?|[—–-]{1,3})?\s{2,}\S")
    lines = text.split("\n")
    body = [l for l in lines if len(l.strip()) > 20]
    if any(date.match(l) for l in lines) or not body:
        return text
    lead = min(len(l) - len(l.lstrip()) for l in body)
    if lead >= 8:
        return text
    return "\n".join(l if re.fullmatch(r"\s*[—–-]?\s*\d{1,4}\s*[—–|-]*\s*", l) or not l.strip()
                     else " " * (12 - lead) + l for l in lines)


NOTE_START = re.compile(r"^\s*(?:Acte|Sign|Non |Suit|Une mention|Le bas|En marge|En (?:grande )?partie|Témoin|Les témoins|Même)")
LATE_LAYOUT = False          # volumes 5-8 (set in main): continuation lines indented only 4-5 spaces


def normalise(text):
    out = []
    for l in text.split("\n"):
        l = l.rstrip()
        l = re.sub(r"^(\s{0,6}(?:\S{1,3}\s+)?[a-zéû]{2,6}) \.(?=\s)", r"\1.", l)               # margin "oct ." -> "oct."
        l = re.sub(r"[\u0590-\u08ff\u200e\u200f\u202a-\u202e]+", "", l)   # stray Arabic/bidi glyphs
        l = re.sub(r"^(\s{0,8})s\s?\.\s?d\s?\.(?=\s)", r"\1s. d.", l)              # margin "s. d ." -> "s. d."
        l = re.sub(r"^(\s*1[56]\d\d)\s?\.\s*$", r"\1", l)              # year heading "1606 ."
        l = re.sub(r"^(\s{0,3})s\s?\.\s?d\s?\.", r"\1s. d.", l)
        l = re.sub(r"^(\s{0,3})1\s?or\b", r"\g<1>1er", l)                  # OCR "1or" / "1 or"
        l = re.sub(r"^(\s{0,3}\d{1,2})\s?\(\s?\?\)", r"\1", l)              # "9( ?)" -> "9"
        l = re.sub(r"^(\s{0,3}\d{1,2}) \(\s?\?\) ([a-zéû]{3,9})\s?\.?", r"\1 \2.", l)  # "2 ( ?) mars ."
        l = re.sub(r"^(\s{0,3})\.\.\s([a-zéû]{3,9}\.)", r"\1\2", l)             # ".. fév." (day lost)
        l = re.sub(r"^(\s{0,3})(?:\d(?=\d\d\s+[—–-]))", r"\1", l)                # "926 —" -> "26 —"
        l = re.sub(r"^(\s{0,3})\(\s?(\d{1,2}\s+[—–-])", r"\1\2", l)            # "(4  —" -> "4  —"
        l = re.sub(r"^[-.]?(\d{1,2})[.]?(?=\s{3,}\S)", r"\1", l)           # margin "26." / "-16" / ".27"
        l = re.sub(r"^(\S.{0,14}?\s{2,})\. (?=En )", r"\1", l)            # "15     . En italien"
        l = re.sub(r"^(\d{1,2}(?:er)?(?:\s+[a-zéû]{2,6}\.?)?\s{3,})(\d{3,4})(\s+[A-Z])", r"\1(\2)\3", l)
        l = re.sub(r"\bEn(italien|latin|espagnol)\b", r"En \1", l)
        l = re.sub(r"\bEn (italien|latin|espagnol|provençal)\b\s*[.,]?\s*(?:[-—–~]+\s*)*", r"En \1. — ", l)
        m = re.match(r"^(\s*\S.{0,14}?\s{2,}|\s*)(.*)$", l)
        lead, body = m.group(1), m.group(2)
        body = re.sub(r"(?<=\w) +(?=[,.;:)\]](?!\.\.))", "", body)   # "Natale ," -> "Natale,"
        body = re.sub(r"(?<=[(\[]) +", "", body)                      # "( de" -> "(de"
        body = re.sub(r"(?<=[»)\]]) +(?=[,.;:](?!\.\.))", "", body)      # "» ." -> "»."
        body = re.sub(r"(?<=[A-Za-zÀ-ÿ]) - (?=[A-Za-zÀ-ÿ])", "-", body)  # "Ste - Anne", "vice - consul"
        body = re.sub(r"(?<=\w) +(?=-\w)", "", body)                  # "celui -ci" -> "celui-ci"
        body = re.sub(r"(?<=\w-) +(?=\w)", "", body)                  # "Sta- Maria" -> "Sta-Maria"
        body = re.sub(r"(?<=\w-) +(?=ci\b|là\b)", "", body)           # "celui - ci"
        out.append(lead + body)
    # A margin date printed (or OCR'd) on its own line: join it to the first line of its act.
    date_only = re.compile(r"^\s{0,3}(\d{1,2}(?:er)?|1er)(?:\s+[a-zéû]{2,6}\.?)?(?:\s*[—–-])?\s*$")
    merged, i = [], 0
    while i < len(out):
        l = out[i]
        if date_only.match(l):
            # the act it belongs to starts on one of the next lines; continuation lines of the
            # previous act may come in between (the OCR misplaced the date)
            j, skipped = i + 1, []
            while j < len(out) and len([k for k in skipped if out[k].strip()]) < 3:
                if out[j].strip() and re.match(r"^\s{6,}(?:En\s+\w+\s*\.|[A-ZÉÀ«(])", out[j]):
                    break
                skipped.append(j)
                j += 1
            prev = next((k for k in range(len(merged) - 1, max(-1, len(merged) - 4), -1) if merged[k].strip()), None)
            if (prev is not None and re.match(r"^\s{6,}En \w+\. — ", merged[prev])
                    and not (j < len(out) and re.match(r"^\s{6,}En \w+\. — ", out[j]))):
                tok = l.strip()            # the date of an act that starts just above it
                merged[prev] = tok + " " * max(4, 12 - len(tok)) + merged[prev].strip()
                i += 1
                continue
            if j < len(out) and all(not out[k].strip() or re.match(r"^\s{6,}[a-zà-ÿ(.\d]|^\s*[-—–.]+\s*$", out[k]) for k in skipped):
                tok = l.strip()
                merged.extend(out[k] for k in skipped)
                merged.append(tok + " " * max(4, 12 - len(tok)) + out[j].strip())
                i = j + 1
                continue
        merged.append(l)
        i += 1
    # A margin date in front of a continuation line (lower case): it belongs to the next act,
    # whose first line ("En italien. — ...") came out without a date.
    entry = re.compile(r"^(\s{0,3}(?:1er|\d{1,2})(?:\s+[a-zéû]{2,6}\.?)?"
                       + (r"(?:\s*[—–-]+){0,3})" if LATE_LAYOUT else r"(?:\s*[—–-])?)") + r"(\s{3,})(?=\S)")
    lang_only = re.compile(r"^\s*En \w+\. —\s*$")
    for i, l in enumerate(merged):
        m = entry.match(l)
        if not m:
            continue
        rest = l[m.end():]
        if not rest.strip(" -—–"):
            rest = "x"                     # a dash alone after the date: treat like a continuation
        nxt_i = next((k for k in range(i + 1, min(i + 4, len(merged))) if merged[k].strip()), None)
        # "27 sept.   Stefano di Burgio..." / "   En italien. —" : the language note was split off
        if nxt_i is not None and lang_only.match(merged[nxt_i]) and not rest.startswith("En "):
            k2 = next((k for k in range(nxt_i + 1, min(nxt_i + 3, len(merged))) if merged[k].strip()), None)
            if k2 is not None and re.match(r"^\s+[a-zà-ÿ]", merged[k2]):
                merged[i] = l[:m.end()] + merged[nxt_i].strip() + " " + rest
                merged[nxt_i] = ""
                continue
        # "18   En italien. —" / "   faire venir..." (end of the previous act) / "      Ettor Cuigliandro...":
        if lang_only.match(rest) and nxt_i is not None and (re.match(r"^\s+[a-zà-ÿ]", merged[nxt_i])
                                                         or (LATE_LAYOUT and re.match(r"^\s{3,8}\S", merged[nxt_i]))):
            k2 = next((k for k in range(nxt_i + 1, min(nxt_i + 3, len(merged))) if merged[k].strip()), None)
            if k2 is not None and re.match(r"^\s{14,}[A-ZÉ(]", merged[k2]):
                cont = merged[nxt_i]
                merged[nxt_i] = l[:m.end()] + rest.strip() + " " + merged[k2].strip()
                merged[i] = cont
                merged[k2] = ""
                continue
        if not re.match(r"[a-zà-ÿ,;\d]", rest) and (re.match(r"E[nt]\s*\w+\s*\.\s*[—–-]", rest) or not nxt_i
                                                    or not re.match(r"^\s{3,}En \w+\. —(?:\s|$)", merged[nxt_i])):
            continue
        seen = 0
        for j in range(i + 1, min(i + 14, len(merged))):
            nxt = merged[j]
            seen += bool(nxt.strip())
            if seen > 6:
                break
            prev_text = next((merged[k] for k in range(j - 1, i, -1) if merged[k].strip()), rest).rstrip()
            starts_act = (re.match(r"^\s{3,}En \w+\. —(?:\s|$)", nxt)
                          or (re.match(r"[a-zà-ÿ,;\d]", rest) and prev_text.endswith((".", ")", "»"))
                              and re.match(r"^\s{6,}(?:\(\s?\d+(?:\s?v\s?[°*º]?)?\s?\)|\([A-Z]|[A-ZÉ][a-zéè]*\.? [A-ZÉ]|[A-ZÉ][a-zéè]+[ ,])", nxt)))
            if starts_act and not re.match(r"^\s*En \w+\. —", nxt) and NOTE_START.match(nxt):
                starts_act = False
            if (not starts_act and LATE_LAYOUT and re.match(r"[a-zà-ÿ,;\d]", rest)
                    and not prev_text.endswith(("-", ",")) and re.match(r"^\s{8,}[A-ZÉ][a-zéè]+ [A-ZÉ(]", nxt)
                    and not NOTE_START.match(nxt)):
                # volumes 5-8: an act without its date starts with a paragraph indent
                starts_act = True
            if starts_act:
                tok = re.sub(r"\s+", " ", m.group(1).strip())
                merged[j] = tok + " " * max(4, 12 - len(tok)) + nxt.strip()
                merged[i] = " " * len(m.group(0)) + l[m.end():]
                break
            if nxt.strip() and not re.match(r"^\s{3,}", nxt):
                break
    return "\n".join(merged)


MONTH_HEAD = re.compile(r"^\s{0,4}(?P<m>Janvier|F[ée]vrier|Mars|Avril|Mai|Juin|Juillet|Ao[uû]t|Septembre|Octobre|"
                        r"Novembre|D[ée]cembre)\s*\.?(?:\s{2,}(?P<y>1[5-7]\d\d)(?:\s*[-–]\s*\d{0,4})?)?\s*$")
MONTH_ABBR = {"janvier": "janv.", "février": "fév.", "fevrier": "fév.", "mars": "mars", "avril": "avril",
              "mai": "mai", "juin": "juin", "juillet": "juil.", "août": "août", "aout": "août",
              "septembre": "sept.", "octobre": "oct.", "novembre": "nov.", "décembre": "déc.", "decembre": "déc."}


def acrobat_margin(pdf, page, side):
    """Volume 4: the photocopy's own OCR layer (Acrobat) reads the bold margin digits better than
    Tesseract (which takes 1 for 4 and 2 for 9). Return its margin lines for one half of the
    PDF page, in the form of margin_ocr.json, so that image_months() can compare the readings."""
    import ocr_volume as OV
    info = subprocess.run(["pdfinfo", "-f", str(page), "-l", str(page), str(pdf)], capture_output=True,
                          text=True).stdout
    m = re.search(r"Page\s+%d size:\s+([\d.]+) x ([\d.]+)" % page, info)
    if not m:
        return None
    pw, ph = float(m.group(1)), float(m.group(2))
    try:
        g = OV.find_gutter(pdf, page, int(pw))
    except Exception:
        g = int(pw / 2)
    x0, w = (0, g) if side == "L" else (g, int(pw) - g)
    t = subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), "-x", str(x0), "-y", "0",
                        "-W", str(w), "-H", str(int(ph)), str(pdf), "-"], capture_output=True, text=True).stdout
    rows = [re.sub(r"^(\s*)[iIl](\d)", r"\g<1>1\2", re.sub(r"^(\s*)(\d)[iIl](?=\s)", r"\g<1>\g<2>1", l))
            for l in t.split("\n") if l.strip()]      # « i7 » for 17, « 2i » for 21
    head = None
    for l in rows[:3]:
        w = l.split()
        if w and w[0][:1].isupper() and fuzzy_month(w[0]):
            head = fuzzy_month(w[0])                 # running head « Février ... 1621 »
            break
    lines = []
    for k, l in enumerate(rows):
        s2 = l.strip()
        y = (k + 1) / (len(rows) + 1)
        if re.match(r"^\s{0,8}(?:1er|1°|\d{1,2})\s*[—_-]", l) or re.match(r"^\s{0,8}\d{1,2}\s+[a-zé]{3,5}\.?\s", l):
            lines.append([max(y, 0.1), s2[:24], 0.0])
        elif len(s2.split()) == 1 and s2[:1].isupper() and k > 1:
            lines.append([max(y, 0.1), s2[:24], 0.0])
    return {"head": head, "lines": lines}


def fix_first_of_month(text):
    """The superscript of « 1er » makes the OCR read « 47 » or « 17 ». 47 is never a day; 17 is
    taken for 1er only between two acts of the 1st."""
    lines = text.split("\n")
    pat = re.compile(r"^(\s{0,3})(1er|\d{1,2})(?=(?:\s+(?:janv|f[ée]v|mars|avril|mai|juin|juil|ao[uû]t|sept|oct|nov|d[ée]c)"
                     r"[a-zéû]*\.?)?(?:\s*[—–-]+)?\s{2,}[^a-zà-ÿ\s])")     # not « 47 aspres » (an amount)
    marks = [(i, m) for i, l in enumerate(lines) for m in [pat.match(l)] if m]
    for k, (i, m) in enumerate(marks):
        tok = m.group(2)
        nxt = marks[k + 1][1].group(2) if k + 1 < len(marks) else None
        prv = marks[k - 1][1].group(2) if k else None
        nxt_n = 1 if nxt == "1er" else int(nxt) if nxt else None
        if tok == "47" or (tok == "17" and prv in ("1er", "1") and nxt in ("1er", "1")):
            lines[i] = m.group(1) + "1er" + lines[i][m.end():]
    return "\n".join(lines)


RUNNING_TITLE = re.compile(r"LA\s+FRANCE\s+EN|EN\s+TUNISIE\s+AU|ANALYSE\s+DES\s+ACTES|CORRESPONDANCE")
MONTH_NAMES = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
               "octobre", "novembre", "décembre"]


def fuzzy_month(word):
    """A month name read by an OCR: exact for the short names (mai, mars, juin, août: « Marc »,
    « Main » are words of the text), close enough for the long ones (« Décemhre »)."""
    import difflib
    key = re.sub(r"[^a-zéèûô]", "", word.lower().replace("1", "l").replace("0", "o"))
    key = {"aout": "août", "fevrier": "février", "decembre": "décembre"}.get(key, key)
    if key in MONTH_NAMES:
        return key
    hit = difflib.get_close_matches(key, [m for m in MONTH_NAMES if len(m) >= 6], n=1, cutoff=0.75)
    return hit[0] if hit and abs(len(hit[0]) - len(key)) <= 1 else None


def margin_entries(page_ocr):
    """The margin of the page image (margin_ocr.json) as a list of ("H", month) headings and
    ("D", day) dates, top to bottom. Lines starting right of the margin are text (« 8 réaux »)."""
    rows = [r + [None] * (3 - len(r)) for r in page_ocr.get("lines", [])]
    xs = sorted(x for y, t, x in rows if x is not None and re.match(r"(1er|1°|\d{1,2})\s*[—–-]", t))
    margin = xs[len(xs) // 5] if xs else None
    seq = []
    for y, t, x in rows:
        if margin is not None and x is not None and x > margin + 0.015:
            continue
        m = re.match(r"(1er|1°|\d{1,2})\b", t)
        if m:
            d = 1 if not m.group(1).isdigit() else int(m.group(1))
            if 40 <= d <= 49:
                d -= 30                       # a « 1 » read as « 4 » (« 43 » for 13)
            if d <= 31:
                seq.append(("D", d))
        elif y >= 0.09 and len(t.split()) == 1 and t[:1].isupper() and fuzzy_month(t):
            seq.append(("H", fuzzy_month(t)))
    return seq


def confusable(a, b, conf):
    """True when the text's day b may be a misreading of the image's day a (« 44 » for 14)."""
    if a == b:
        return True
    if not conf:
        return False
    sa, sb = str(a), str(b)
    return len(sa) == len(sb) and all(x == y or conf.get(y) == x for x, y in zip(sa, sb))


def image_months(text, page_ocr, conf=None):
    """Put back the month headings the Google OCR lost, from margin_ocr.json (Tesseract on the
    left part of the page image): the running head names the month of the first act on the
    page, and each heading the month of the act that follows it. A heading line is inserted
    before the matching act, unless the act already carries a month."""
    if not page_ocr:
        return text
    lines = text.split("\n")
    act_re = re.compile(r"^\s{0,3}(1er|1°|\d{1,2})(?=(?:\s*[—–=-]+)?\s{2,}\S|\s*[—–=-]+\s*$|\s*$)")
    acts = [(i, 1 if m.group(1).startswith("1e") or m.group(1) == "1°" else int(m.group(1)))
            for i, l in enumerate(lines) for m in [act_re.match(l)] if m and not RUNNING_TITLE.search(l)]
    if not acts:
        return text
    n = max(1, len(lines))

    def day_of(tok):
        m = re.match(r"(1er|1°|\d{1,2})", tok)
        return None if not m else 1 if not m.group(1).isdigit() else int(m.group(1))

    targets = {}
    if page_ocr.get("head"):
        targets[acts[0][0]] = page_ocr["head"]
    # the image's margin as a sequence of headings and days, aligned with the acts found in
    # the Google text by their days (longest common subsequence)
    seq = margin_entries(page_ocr)
    ds = [k for k, e in enumerate(seq) if e[0] == "D"]
    A, B = [seq[k][1] for k in ds], [a[1] for a in acts]
    L = [[0] * (len(B) + 1) for _ in range(len(A) + 1)]
    for i in range(len(A) - 1, -1, -1):
        for j in range(len(B) - 1, -1, -1):
            L[i][j] = L[i + 1][j + 1] + 1 if confusable(A[i], B[j], conf) else max(L[i + 1][j], L[i][j + 1])
    pair, i, j, fixed_days_pre = {}, 0, 0, {}
    while i < len(A) and j < len(B):
        if confusable(A[i], B[j], conf):
            if A[i] != B[j]:
                fixed_days_pre[acts[j][0]] = A[i]      # the other OCR's reading of a confusable day
            pair[ds[i]] = j
            i, j = i + 1, j + 1
        elif L[i + 1][j] >= L[i][j + 1]:
            i += 1
        else:
            j += 1
    # a day the two OCRs read differently, between two days they agree on: take the image's
    # reading when it fits the order of the days (Google « 27 » between 11 and 13, image « 12 »)
    fixed_days = dict(fixed_days_pre)
    anchors = sorted(pair.items())                       # (seq index, act index)
    bounds = [(-1, -1)] + anchors + [(len(seq), len(acts))]
    for (s0, a0), (s1, a1) in zip(bounds, bounds[1:]):
        img = [seq[q][1] for q in range(s0 + 1, s1) if seq[q][0] == "D"]
        txt = list(range(a0 + 1, a1))
        if len(img) == 1 and len(txt) == 1:
            lo = acts[a0][1] if a0 >= 0 else 1
            hi = acts[a1][1] if a1 < len(acts) else 31
            if lo <= img[0] <= hi and not lo <= acts[txt[0]][1] <= hi:
                fixed_days[acts[txt[0]][0]] = img[0]
    for k, e in enumerate(seq):
        if e[0] != "H":
            continue
        nxt = next((pair[q] for q in range(k + 1, len(seq)) if seq[q][0] == "D" and q in pair), None)
        prv = next((pair[q] for q in range(k - 1, -1, -1) if seq[q][0] == "D" and q in pair), None)
        first_d_after = next((q for q in range(k + 1, len(seq)) if seq[q][0] == "D"), None)
        if nxt is not None and first_d_after in pair:
            targets[acts[nxt][0]] = e[1]
        elif prv is not None and prv + 1 < len(acts):
            targets[acts[prv + 1][0]] = e[1]          # the day under the heading was misread
    out = []
    for i, l in enumerate(lines):
        if i in fixed_days:
            l = re.sub(r"^(\s{0,3})(?:1er|1°|\d{1,2})", lambda m: m.group(1) + ("1er" if fixed_days[i] == 1
                       else str(fixed_days[i])), l, count=1)
        if i in targets and not re.match(r"^\s{0,3}(?:1er|1°|\d{1,2})\s+[a-zéû]{3,9}\.?(?:\s|$)", l):
            out.append(targets[i].capitalize())
        out.append(l)
    return "\n".join(out)


def month_headings(text, years=None):
    """Volumes 5-8 print the month as a heading and the year at the right edge of the page,
    with only the day in the margin. Rewrite them as in volumes 2-3: the year on its own line
    at the left, and the month after the day of the first act under the heading."""
    out, pending = [], None
    src = text.split("\n")
    leader = re.compile(r"\.{3,}\s*(?:p\.\s*)?[\d.,]+\s*$")
    list_items = set()
    for k, l in enumerate(src):
        if re.match(r"^\s{0,8}\d{1,2}\s*[—–-]", l):
            nxt = next((x for x in src[k + 1:k + 3] if x.strip()), "")
            if leader.search(l) or (leader.search(nxt) and not re.match(r"^\s{0,8}\d{1,2}\s*[—–-]", nxt)):
                list_items.add(k)
    for k_line, l in enumerate(src):
        y = re.fullmatch(r"\s*(1[5-7]\d\d)(?:\s*[-–]\s*\d{1,4})?\s*", l)
        if y and len(l) - len(l.lstrip()) > 12:
            out.append("  " + y.group(1))
            continue
        if k_line in list_items:
            out.append("         " + l)        # an item of a numbered list (« 12 — Fran°° Sannuto .... 300 »)
            continue
        if re.match(r"^\s{0,6}(?:1er|\d{1,2})\s+(?:janv|f[ée]v|mars|avril|mai|juin|juil|ao[uû]t|sept|oct|nov|d[ée]c)"
                    r"[a-zéû]*\.?,?\s+(?:1[5-7]\d\d\b|(?!En\s)[a-zà-ÿ'’]{2,})", l):
            out.append("         " + l)        # "30 juin 1633. Signé", "27 mai jusqu'au": a date inside the text
            continue
        m = MONTH_HEAD.match(l)
        fz = None if m else re.match(r"^\s{0,4}(?P<w>[A-ZÉ][a-zéèûô]{2,9})\.?(?:\s{3,}\S{0,4}\s*(?P<y>1[5-7]\d\d)"
                                     r"(?:\s*[-–]\s*\d{0,4})?)?\s*$", l)
        if fz and fuzzy_month(fz.group("w")):
            m = {"m": fuzzy_month(fz.group("w")), "y": fz.group("y")}
        if m:
            m = {"m": m["m"], "y": m["y"]}
            if m["y"] and years and not years[0] <= int(m["y"]) <= years[1]:
                m["y"] = None                  # misread year ("1688" on a page of 1645)
            pending = MONTH_ABBR[m["m"].lower()]
            out.append(f"  {m['y']}" if m["y"] else "")
            # the OCR sometimes reads the heading after the day of the act it heads:
            # " 3   -" / "Juillet" / "   En italien. — ..."
            back = [k for k in range(len(out) - 2, max(-1, len(out) - 6), -1) if out[k].strip()][:2]
            for k in back:
                d = re.match(r"^(\s{0,3})(1er|1°|\d{1,2})(?:\s*[—–-]+)?(\s*(?:En \w+\. —)?\s*)$", out[k])
                if d and int(re.match(r"\d+", d.group(2)).group()) <= 31:
                    out[k] = f"{d.group(1)}{d.group(2)} {pending}" + (d.group(3) if d.group(3).strip() else "")
                    pending = None
                    break
            continue
        if pending and re.match(r"^\s{0,3}(?:1er|1°|\d{1,2})\s+(?:janv|f[ée]v|mars|avril|mai|juin|juil|ao[uû]t|sept|oct|nov|d[ée]c)", l):
            pending = None                     # the act carries its own month
        d = re.match(r"^\s{0,3}(1er|1°|\d{1,2})(?:\s*[—–=-]+)?\s{2,}(?=\S)", l)
        if d and RUNNING_TITLE.search(l):
            d = None                           # "2     LA FRANCE EN TUNISIE ..." : the page number
        if d and pending and int(re.match(r"\d+", d.group(1)).group()) <= 31:
            tok = f"{d.group(1)} {pending}"
            out.append(tok + " " * max(3, 12 - len(tok)) + l[d.end():])
            pending = None
            continue
        out.append(l)
    return "\n".join(out)


def raw_volume(vid, vol):
    """A single scan with a fixed page offset and a good text layer (volume 1): pages are
    written as pdftotext gives them, numbered by printed page."""
    out_dir = ROOT / "text" / f"vol{vid}" / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.txt"):
        f.unlink()
    pdf, off = ROOT / vol["file"], vol["page_offset"]
    index, fm = {}, 0
    for p in range(1, pdf_pages(pdf) + 1):
        t = layout_text(pdf, p)
        if len(t.strip()) < 40 or re.search(r"PLEASE DO NOT\s+REMOVE|UNIVERSITY OF TORONTO", t):
            continue
        if p > off:
            lab = f"p{p - off:03d}"
        else:
            fm += 1
            lab = f"fm{fm:02d}"
        (out_dir / f"{lab}.txt").write_text(t, encoding="utf-8")
        index[lab] = {"file": vol["file"], "pdf_page": p}
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"vol {vid}: {len(index)} pages written to {out_dir.relative_to(ROOT)}")


def main():
    vid = int(sys.argv[1])
    cfg = json.loads((ROOT / "volumes.json").read_text(encoding="utf-8"))
    vol = next(v for v in cfg["volumes"] if v["id"] == vid)
    if not vol.get("scans"):
        return raw_volume(vid, vol)
    scans = vol["scans"]
    global LATE_LAYOUT
    LATE_LAYOUT = bool(vol.get("month_headings"))
    mo_path = ROOT / "text" / f"vol{vid}" / "margin_ocr.json"
    margin_ocr = json.loads(mo_path.read_text(encoding="utf-8")) if mo_path.exists() else {}
    out_dir = ROOT / "text" / f"vol{vid}" / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.txt"):
        f.unlink()

    seq = []                               # (file, pdf_page, text, detected number)
    sides = {}                             # two-page photocopies: (file, pdf_page) of a half -> "L"/"R"
    for name in scans:
        pdf = ROOT / name
        for p in range(1, pdf_pages(pdf) + 1):
            if vol.get("two_up"):
                # two book pages per PDF page, OCR'd half by half by ocr_two_up.py
                part = Path(name).stem.rsplit("_", 1)[-1]
                for side in "LR":
                    f = ROOT / "text" / f"vol{vid}" / "halves" / f"{part}-{p:03d}{side}.txt"
                    t = f.read_text(encoding="utf-8") if f.exists() else ""
                    sides[len(seq)] = side
                    pinned = vol.get("page_numbers", {}).get(f"{part}-{p:03d}{side}")
                    seq.append([name, p, t, pinned or header_number(t)])
                continue
            t = layout_text(pdf, p)
            seq.append([name, p, t, header_number(t)])

    # Anchors: pages whose header number fits an increasing run (longest chain where page
    # numbers never grow faster than PDF pages). Pages between anchors are numbered in
    # order; surplus pages between two anchors are plates (the ones with least text).
    first_body = vol["first_body_page"]                 # [part index, pdf page] of printed page 1
    start = next(i for i, s in enumerate(seq) if s[0] == scans[first_body[0]] and s[1] == first_body[1]
                 and (len(first_body) < 3 or sides.get(i) == first_body[2]))   # [part, page, "L"/"R"]
    seq[start][3] = 1
    cand = [(i, seq[i][3]) for i in range(start, len(seq)) if seq[i][3]]
    best = [1] * len(cand); prev = [-1] * len(cand)
    for a in range(len(cand)):
        for b in range(a):
            (ia, na), (ib, nb) = cand[a], cand[b]
            if 0 < na - nb <= ia - ib and best[b] + 1 > best[a]:
                best[a], prev[a] = best[b] + 1, b
    a = max(range(len(cand)), key=lambda k: best[k]); chain = []
    while a >= 0:
        chain.append(cand[a]); a = prev[a]
    chain.reverse()
    if chain[0][0] != start:
        chain.insert(0, (start, 1))
    labels = [None] * len(seq)
    def text_len(i):
        return len(seq[i][2].strip())
    for (i1, n1), (i2, n2) in zip(chain, chain[1:] + [(len(seq), None)]):
        labels[i1] = f"p{n1:03d}"
        between = [k for k in range(i1 + 1, i2) if text_len(k) >= 40]
        need = (n2 - n1 - 1) if n2 else len(between)
        if len(between) > need:
            keep = sorted(sorted(between, key=text_len, reverse=True)[:need])
            for k in between:
                if k not in keep:
                    labels[k] = (f"plate-{seq[k][1]:03d}" if seq[k][0] == scans[0] else
                                 f"plate-{scans.index(seq[k][0]) + 1}-{seq[k][1]:03d}") + sides.get(k, "")
                    print(f"  plate: {seq[k][0]} p.{seq[k][1]}", file=sys.stderr)
            between = keep
        elif len(between) < need:
            every = list(range(i1 + 1, i2))
            if len(every) == need:
                # a blank page (verso before a new section) fills the gap: number it, it is not written
                between = every
            else:
                print(f"  ! {need - len(between)} page(s) missing between {n1} and {n2}", file=sys.stderr)
        for j, k in enumerate(between):
            labels[k] = f"p{n1 + 1 + j:03d}"
    fm = 0
    for i in range(start):
        if len(seq[i][2].strip()) >= 40:
            fm += 1
            labels[i] = f"fm{fm:02d}"

    # Pages whose Google OCR layout is scrambled (curved pages): OCR the page image with
    # Tesseract instead (cached in text/vol<N>/tesseract/, so it runs once).
    thr = vol.get("tesseract_threshold")
    if thr:
        from concurrent.futures import ThreadPoolExecutor
        import page_quality
        cache_dir = ROOT / "text" / f"vol{vid}" / "tesseract"
        cache_dir.mkdir(exist_ok=True)
        todo = []
        for (name, p, t, n), lab in zip(seq, labels):
            if lab and re.fullmatch(r"p\d+", lab) and vol["analysis_pages"][0] <= int(lab[1:]) <= vol["analysis_pages"][1] \
                    and (page_quality.score(normalise(t))[0] >= thr or int(lab[1:]) in vol.get("tesseract_pages", [])) \
                    and not (cache_dir / f"{lab}.txt").exists():
                todo.append((lab, name, p))
        def run(job):
            lab, name, p = job
            (cache_dir / f"{lab}.txt").write_text(tesseract_page(ROOT / name, p), encoding="utf-8")
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(run, todo))
        if todo:
            print(f"  tesseract: {len(todo)} pages OCR'd", file=sys.stderr)
    fixes_path = ROOT / "text" / f"vol{vid}" / "fixes.json"
    page_fixes = json.loads(fixes_path.read_text(encoding="utf-8")).get("pages", []) if fixes_path.exists() else []
    index = {}
    for k_seq, ((name, p, t, n), lab) in enumerate(zip(seq, labels)):
        if not lab or len(t.strip()) < 40 or re.search(r"CIRCULATION DEPARTMENT|GENERAL LIBRARY - U\.C", t):
            continue
        if lab in vol.get("second_ocr_pages", []):
            # scrambled in this scan (curved page): use the other copy's OCR instead
            t = clean_second((ROOT / vol["text_source"] / f"{lab}.txt").read_text(encoding="utf-8"))
        tess = ROOT / "text" / f"vol{vid}" / "tesseract" / f"{lab}.txt"
        if tess.exists():
            t = transplant_dates(reindent(tess.read_text(encoding="utf-8")), normalise(t), lab)
        hand = ROOT / "text" / f"vol{vid}" / "transcribed" / f"{lab}.txt"
        if hand.exists():
            # pages the OCR scrambled, transcribed by hand from the page image
            t = hand.read_text(encoding="utf-8")
        t = normalise(normalise(t))        # a second pass settles lines the first one moved
        if re.fullmatch(r"p\d+", lab) and vol["analysis_pages"][0] <= int(lab[1:]) <= vol["analysis_pages"][1]:
            # act pages: a "|" is the page gutter or a column rule read by the OCR (in the
            # two-column indexes it separates the columns and is kept)
            t = "\n".join(re.sub(r"(?:(?<=\s)|^)\|(?=\s|$)", " ", l).rstrip() for l in t.split("\n"))
            # margin '1"', '1**', '1°' (in the documents, "1º" numbers the first document)
            t = re.sub(r"(?m)^(\s{0,3})(?:[14][19]?(?:\"|\*\*?|°|º)|[Ll]er|1 er|1r|er)(?=\s+(?:[—–-]|[A-ZÉ(«]|En\s|janv|f[ée]v|mars|avril|mai|juin|juil|ao[uû]t|sept|oct|nov|d[ée]c|$))",
                       r"\g<1>1er", t)
            if vol.get("month_headings"):
                if not vol.get("ocr_digit_confusions"):      # vol. 4: « 47 » is 17 there, not 1er
                    t = fix_first_of_month(t)
                po = margin_ocr.get(lab)
                if vol.get("two_up") and k_seq in sides:
                    # the photocopy's Acrobat layer for the margin days and headings; the running
                    # head from it or, failing that, from Tesseract on the top of the half page
                    acro = acrobat_margin(ROOT / name, p, sides[k_seq]) or {"head": None, "lines": []}
                    acro["head"] = acro["head"] or (po or {}).get("head")
                    po = acro
                t = image_months(t, po, vol.get("ocr_digit_confusions"))
                t = month_headings(t, (int(vol["period_start"][:4]), int(vol["period_end"][:4]) + 3))
        for f in page_fixes:          # hand corrections of the page text, checked against the scan
            if f["page"] == lab:
                if f["old"] not in t:
                    print(f"  ! fixes.json: {f['old'][:40]!r} not found on {lab}", file=sys.stderr)
                t = t.replace(f["old"], f["new"])
        (out_dir / f"{lab}.txt").write_text(t, encoding="utf-8")
        index[lab] = {"file": name, "pdf_page": p}
        if k_seq in sides:
            index[lab]["side"] = sides[k_seq]          # left or right half of the PDF page
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"vol {vid}: {len(index)} pages written to {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
