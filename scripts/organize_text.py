#!/usr/bin/env python3
"""Write the organised text files of a volume from its page files.

Input:  text/vol<N>/pages/*.txt (scripts/extract_pages.py) and the section page ranges in
        volumes.json; hand fixes in text/vol<N>/fixes.json.
Output (text/vol<N>/):
  front_matter.txt   title pages, foreword, bibliography
  acts.txt           the analysis of the acts, one block per act, with markers (see README.md)
  documents.txt      documents printed in full (annexes, or Parts II-III of volume 1)
  index_*.txt        the book's tables (names, ships, slaves...), columns un-interleaved
  glossary.txt, contents.txt, corrections.txt, plates.txt   where the volume has them

acts.txt is what scripts/build_db.py loads, so it can be corrected by hand through
fixes.json and regenerated.

Usage: python3 scripts/organize_text.py 2
"""
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_db as B  # noqa: E402

MONTHS_RE = "|".join(B.MONTH_NAMES)
MENTIONED_DATE = re.compile(rf"\b(1er|\d{{1,2}})\s+({MONTHS_RE})\s+(1[56]\d\d)\b", re.I)
MENTIONED_MONTH = re.compile(rf"\b(?:en|de|d')\s*({MONTHS_RE})\s+(1[56]\d\d)\b", re.I)
FOLIO = re.compile(r"^\W*(?:E[nt]i?\s*\w+\W*)?[({]\s*(\d{1,4})\s*\)")


# ---------------------------------------------------------------- page helpers

def load_pages(vid):
    d = ROOT / "text" / f"vol{vid}" / "pages"
    index = json.loads((d / "index.json").read_text(encoding="utf-8"))
    return d, index


def page_label(n):
    return f"p{n:03d}"


def split_columns(text):
    """Un-interleave a two-column page from pdftotext -layout: left column, then right."""
    lines = text.split("\n")
    body = [l for l in lines if len(l.strip()) > 3]
    if not body:
        return text
    width = max(len(l) for l in body)
    best, best_x = -1, None
    for x in range(int(width * 0.3), int(width * 0.7)):
        free = sum(1 for l in body if len(l) <= x or l[max(0, x - 1):x + 2].strip() == "")
        if free > best:
            best, best_x = free, x
    if best < len(body) * 0.6:
        return text
    left, right = [], []
    for l in lines:
        if len(l) <= best_x:
            left.append(l.rstrip())
            continue
        cut = best_x
        # move the cut into the nearest run of spaces
        while cut > 0 and l[cut - 1:cut + 1].strip():
            cut -= 1
        left.append(l[:cut].rstrip())
        right.append(l[cut:].strip())
    def clean(col):
        return "\n".join(c.strip() for c in col if c.strip())
    return clean(left) + "\n" + clean(right)


def join_lines(lines):
    """Rejoin a column of text lines into paragraphs (hyphenated words rejoined)."""
    out, para = [], []
    for l in lines:
        s = l.strip()
        if not s:
            if para:
                out.append(B.join_text(para))
                para = []
            continue
        para.append(s)
    if para:
        out.append(B.join_text(para))
    return out


def section_text(pages_dir, index, labels, columns=False, strip_header=True):
    chunks = []
    for lab in labels:
        f = pages_dir / f"{lab}.txt"
        if not f.exists():
            continue
        t = f.read_text(encoding="utf-8")
        lines = t.split("\n")
        if strip_header:
            lines = [l for l in lines if not B.PAGE_HDR_RE.match(l) and not re.fullmatch(r"[\s\-–—~.:|_\d]{1,12}", l)]
        t = "\n".join(lines)
        if columns:
            t = split_columns(t)
        src = index.get(lab, {})
        chunks.append(f"[page {lab[1:] if lab.startswith('p') else lab} | scan: {Path(src.get('file', '?')).name} "
                      f"p. {src.get('pdf_page', '?')}]\n" + t.strip("\n"))
    return "\n\n".join(chunks) + "\n"


def labels_in(index, first, last):
    return [page_label(n) for n in range(first, last + 1) if page_label(n) in index]


# ---------------------------------------------------------------- second OCR source

TOK = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]")


def tokens(s):
    return TOK.findall(s.replace("’", "'"))


def detok(ws):
    text = ""
    for w in ws:
        if not text:
            text = w
        elif re.fullmatch(r"[,.;:)\]!?]", w) or text.endswith(("(", "[", "'", "-")) or w == "-":
            text += w
        else:
            text += " " + w
    return text


def merge_ocr(primary, secondary, freq):
    """Correct obvious OCR slips of `primary` (Google OCR) with `secondary` (Tesseract on
    another copy of the book): a word of the primary text that occurs at most once in the
    whole volume is replaced by the secondary's word when that one is common (>= 3) and
    similar. Numbers are never changed (they are listed for checking instead)."""
    ta, tb = tokens(primary), tokens(secondary)
    sm = difflib.SequenceMatcher(None, [w.lower() for w in ta], [w.lower() for w in tb], autojunk=False)
    out, changes = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        A, Bw = ta[i1:i2], tb[j1:j2]
        if op == "replace" and len(A) == len(Bw):
            for x, y in zip(A, Bw):
                if (x.isalpha() and y.isalpha() and freq[x.lower()] <= 1 and freq[y.lower()] >= 3
                        and difflib.SequenceMatcher(None, x.lower(), y.lower()).ratio() >= 0.6
                        and x[:1].isupper() == y[:1].isupper()):
                    out.append(y)
                    changes.append(f"{x} -> {y}")
                else:
                    out.append(x)
            continue
        out += A
    if not changes:
        return primary, []
    return re.sub(r"\s{2,}", " ", detok(out)), changes


# ---------------------------------------------------------------- acts

def apply_fixes(acts, fixes):
    """Hand fixes keyed by a snippet of the act's text (stable across re-parses):
    split, merge_with_previous, drop, replace, date."""
    def find(snippet, page=None):
        hits = [i for i, a in enumerate(acts) if snippet in a["summary"] and (page is None or a["page"] == page)]
        if len(hits) != 1:
            raise SystemExit(f"fixes.json: snippet {snippet!r} matches {len(hits)} acts")
        return hits[0]
    for f in fixes.get("replace", []):
        i = find(f["find"], f.get("page"))
        acts[i]["summary"] = acts[i]["summary"].replace(f["old"], f["new"])
    for f in fixes.get("split", []):
        i = find(f["find"], f.get("page"))
        a = acts[i]
        k = a["summary"].find(f["at"])
        if k <= 0:
            raise SystemExit(f"fixes.json: split mark {f['at']!r} not found")
        new = dict(a, summary=a["summary"][k:].strip(), date_raw=f.get("date_raw", "(date lost in the scan)"),
                   day_guess=not f.get("date"))
        a["summary"] = a["summary"][:k].strip()
        if f.get("date"):
            set_date(new, f["date"], f.get("note"))
        acts.insert(i + 1, new)
    for f in fixes.get("merge_with_previous", []):
        i = find(f["find"], f.get("page"))
        acts[i - 1]["summary"] = (acts[i - 1]["summary"] + " " + acts[i]["summary"]).strip()
        del acts[i]
    for f in fixes.get("drop", []):
        del acts[find(f["find"], f.get("page"))]
    for f in fixes.get("date", []):
        set_date(acts[find(f["find"], f.get("page"))], f["date"], f.get("note"))
    return acts


def set_date(a, d, note=None):
    parts = [int(x) for x in d.split("-")] if d else []
    a["undated"] = not parts
    a["year"] = parts[0] if parts else a["year"]
    a["month"] = parts[1] if len(parts) > 1 else None
    a["day"] = parts[2] if len(parts) > 2 else None
    a["day_guess"] = False
    a["date_corrected"] = True
    if note:
        a["date_note"] = note


def act_date(a):
    if a["undated"]:
        return f"undated ({a['year']})" if a.get("year") else "undated"
    s = f"{a['year']:04d}"
    if a.get("month"):
        s += f"-{a['month']:02d}"
        if a.get("day"):
            s += f"-{a['day']:02d}"
    return s


def date_status(a):
    if a.get("date_corrected"):
        return "corrected by hand"
    if a.get("date_supplied"):
        return "supplied by the editor (in brackets)"
    if a["undated"]:
        return "undated in the register (s. d.)"
    if a.get("day_guess"):
        return ("not printed or not legible; taken from the previous act"
                if a["date_raw"].startswith("(") else "day reconstructed from a damaged reading")
    return "printed in the margin"


def mentioned_dates(s):
    out = []
    for d, m, y in MENTIONED_DATE.findall(s):
        mi = B.MONTH_NAMES.index(m.lower()) + 1
        out.append(f"{y}-{mi:02d}-{int(1 if d == '1er' else d):02d}")
    for m, y in MENTIONED_MONTH.findall(s):
        out.append(f"{y}-{B.MONTH_NAMES.index(m.lower()) + 1:02d}")
    seen = []
    for x in out:
        if x not in seen and not any(o.startswith(x) for o in out if o != x):
            seen.append(x)
    return seen


def flags(s):
    f = []
    for name, pat in (("struck out", r"biff[ée]"), ("illegible", r"illisible"),
                      ("unfinished", r"inachev[ée]|n'a pas [ée]t[ée] achev"),
                      ("damaged", r"effac[ée]|d[ée]chir[ée]|rong[ée]"),
                      ("copied in full in the annexes", r"in[\s-]*extenso")):
        if re.search(pat, s, re.I):
            f.append(name)
    return f


def write_acts(vol, acts, officials, footnotes, index, out):
    vid = vol["id"]
    regs = {r["number"]: r for r in vol["registers"]}
    off_by_seq = {}
    for o in officials:
        off_by_seq.setdefault(o["after_act_seq"], []).append(o)
    lines = [f"# {vol['title']}",
             "# Analysis of the acts passed at the French consulate in Tunis (Part I of the book).",
             "# One block per act; see README.md for the markers. Generated by scripts/organize_text.py.", ""]
    last_reg = None
    for seq, a in enumerate(acts, 1):
        if a["register"] != last_reg:
            r = regs[a["register"]]
            lines += [f"[register {r['number']}] {r['label']} ({r['date_start']} – {r['date_end']})", ""]
            last_reg = a["register"]
        for o in off_by_seq.get(seq - 1, []):
            lines += [f"[official] {o['name']}, {o['role'].replace('_', '-')} (heading on p. {o['page']})", ""]
        src = index.get(page_label(a["page"]), {})
        s = a["summary"]
        lines.append(f"[act {a['ref']}]")
        lines.append(f"date: {act_date(a)}")
        lines.append(f"date printed: {a['date_raw'] or '—'}")
        lines.append(f"date status: {date_status(a)}")
        if a.get("date_note"):
            lines.append(f"date note: {a['date_note']}")
        lines.append(f"register: {a['register']}")
        lines.append(f"page: {a['page']} (scan: {Path(src.get('file', '?')).name} p. {src.get('pdf_page', '?')})")
        fm = FOLIO.match(s)
        if fm:
            lines.append(f"folio: {fm.group(1)}")
        lm = re.search(r"\bEn (italien|latin|espagnol|provençal|turc|arabe)\b", s)
        lines.append(f"language: {lm.group(1) if lm else 'français'}")
        for role in ("consul", "vice_consul", "chancelier"):
            if a.get(role):
                lines.append(f"{role.replace('_', '-')}: {a[role]}")
        amts = B.extract_amounts(s)
        if amts:
            lines.append("amounts: " + "; ".join(x["raw"] for x in amts))
        md = mentioned_dates(s)
        if md:
            lines.append("dates mentioned: " + ", ".join(md))
        fl = flags(s)
        if fl:
            lines.append("flags: " + ", ".join(fl))
        if a.get("ocr_changes"):
            lines.append("ocr corrections: " + "; ".join(a["ocr_changes"]))
        lines.append("text: " + s)
        lines.append("")
    if footnotes:
        lines += ["[footnotes]", ""]
        for f in footnotes:
            lines.append(f"[footnote p. {f['page']} ({f['marker']})] {f['text']}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def read_acts(path):
    """Inverse of write_acts, used by build_db.py: (acts, officials, footnotes)."""
    acts, officials, footnotes = [], [], []
    cur = None
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        if line.startswith("[act "):
            cur = {"ref": line[5:-1].strip()}
            acts.append(cur)
            continue
        m = re.match(r"^\[official\] (.+), (consul|vice-consul|chancelier) \(heading on p\. (\d+)\)$", line)
        if m:
            officials.append({"name": m.group(1), "role": m.group(2).replace("-", "_"), "page": int(m.group(3)),
                              "after_act_seq": len(acts)})
            cur = None
            continue
        m = re.match(r"^\[footnote p\. (\d+) \((\d+)\)\] (.*)$", line)
        if m:
            footnotes.append({"page": int(m.group(1)), "marker": int(m.group(2)), "text": m.group(3)})
            continue
        if cur is None or not line.strip():
            continue
        k, _, v = line.partition(": ")
        cur[k] = v
    out = []
    for a in acts:
        d = a["date"]
        um = re.match(r"undated(?: \((\d{4})\))?$", d)
        parts = [int(um.group(1))] if um and um.group(1) else [] if um else [int(x) for x in d.split("-")]
        pm = re.match(r"(\d+) \(scan: (.+?) p\. (\S+)\)", a["page"])
        status = a.get("date status", "")
        out.append({
            "ref": a["ref"], "register": a["register"], "summary": a["text"],
            "year": parts[0] if parts else None, "month": parts[1] if len(parts) > 1 else None,
            "day": parts[2] if len(parts) > 2 else None, "undated": bool(um),
            "date_supplied": status.startswith("supplied"), "date_raw": a.get("date printed", ""),
            "day_guess": status.startswith(("not printed", "day reconstructed")),
            "date_note": a.get("date note"), "date_corrected": status.startswith("corrected"),
            "page": int(pm.group(1)), "scan_file": pm.group(2),
            "pdf_page": int(pm.group(3)) if pm.group(3).isdigit() else None,
            "folio": a.get("folio"), "consul": a.get("consul"), "vice_consul": a.get("vice-consul"),
            "chancelier": a.get("chancelier"),
        })
    return out, officials, footnotes


def build_acts(vol):
    vid = vol["id"]
    pv = dict(vol)
    pv["pages_dir"] = f"text/vol{vid}/pages"
    pv.pop("text_source", None)
    acts, officials, footnotes = B.parse_analysis(None, pv)
    fixes_path = ROOT / "text" / f"vol{vid}" / "fixes.json"
    fixes = json.loads(fixes_path.read_text(encoding="utf-8")) if fixes_path.exists() else {}
    # officials are positioned by act count; keep them attached to the act that follows
    for o in officials:
        o["_before"] = acts[o["after_act_seq"]]["summary"] if o["after_act_seq"] < len(acts) else None
    acts = apply_fixes(acts, fixes.get("acts", {}))
    for o in officials:
        o["after_act_seq"] = next((i for i, a in enumerate(acts) if a["summary"] == o["_before"]), len(acts))
    for a in acts:
        a["summary"] = re.sub(r"\s{2,}", " ", a["summary"]).strip()
        # the book prints ½ as a stacked fraction, which the Google OCR drops: "53 écus et d'or"
        fixed = re.sub(r"(\d+ (?:écus|pièces|onces|taris|réaulx|sequins|ducats|livres|cantars)(?: d'or)? et)"
                       r"(?=\s*(?:[,.;)]|d'or|payé|pour|de Sicile|de 8))", r"\1 ½", a["summary"])
        if fixed != a["summary"]:
            a["summary"] = fixed
            a.setdefault("ocr_changes", []).append("½ restored")
    if vol.get("second_ocr"):
        second = dict(vol)
        second.pop("pages_dir", None)
        old, _, _ = B.parse_analysis(None, second)
        freq = Counter(w.lower() for x in acts + old for w in tokens(x["summary"]))
        norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", s.lower())
        by_page = {}
        for o in old:
            by_page.setdefault(o["page"], []).append(o)
        hand = {f.stem for f in (ROOT / "text" / f"vol{vid}" / "transcribed").glob("p*.txt")}
        for a in acts:
            if page_label(a["page"]) in hand:
                continue                 # transcribed from the image: nothing to correct
            cands = [o for p in (a["page"] - 1, a["page"], a["page"] + 1) for o in by_page.get(p, [])]
            if not cands:
                continue
            best = max(cands, key=lambda o: difflib.SequenceMatcher(None, norm(a["summary"])[:250],
                                                                     norm(o["summary"])[:250]).ratio())
            if difflib.SequenceMatcher(None, norm(a["summary"])[:250], norm(best["summary"])[:250]).ratio() < 0.6:
                continue
            # Pages where the Google OCR lost its punctuation and scattered words (curved
            # pages): use the other OCR as the base text and correct it with this one.
            def density(t):            # punctuation attached to a word (OCR specks do not count)
                return len(re.findall(r"\w[,.;]", t)) / max(1, len(t))
            ratio = len(best["summary"]) / max(1, len(a["summary"]))
            if (len(re.findall(r"\w[,.;]", best["summary"])) >= 3 and 0.75 <= ratio <= 1.3
                    and difflib.SequenceMatcher(None, norm(a["summary"]), norm(best["summary"])).ratio() >= 0.75
                    and density(a["summary"]) < 0.6 * density(best["summary"])):
                base = re.sub(r"(?<=\s)[|¦©®ÈÌ](?=\s)|\s+[|¦]\s*$", "", best["summary"])
                a["summary"], ch = merge_ocr(re.sub(r"\s{2,}", " ", base), a["summary"], freq)
                a["ocr_changes"] = a.get("ocr_changes", []) + ["text taken from the second OCR (this page is badly read in the main scan)"] + ch
            else:
                a["summary"], ch = merge_ocr(a["summary"], best["summary"], freq)
                a["ocr_changes"] = a.get("ocr_changes", []) + ch
    for i, a in enumerate(acts, 1):
        a["ref"] = f"{vid}-{a['register']}-{i:04d}"
    return acts, officials, footnotes


def main():
    vid = int(sys.argv[1])
    cfg = json.loads((ROOT / "volumes.json").read_text(encoding="utf-8"))
    vol = next(v for v in cfg["volumes"] if v["id"] == vid)
    out_dir = ROOT / "text" / f"vol{vid}"
    pages_dir, index = load_pages(vid)
    sec = vol["sections"]

    acts, officials, footnotes = build_acts(vol)
    write_acts(vol, acts, officials, footnotes, index, out_dir / "acts.txt")
    print(f"acts.txt: {len(acts)} acts, {len(officials)} officials, {len(footnotes)} footnotes")

    fm = sorted(k for k in index if k.startswith("fm"))
    (out_dir / "front_matter.txt").write_text(
        f"# {vol['title']} — front matter (title pages, foreword)\n\n"
        + section_text(pages_dir, index, fm + labels_in(index, *sec.get("front_matter_pages", [0, -1])),
                       strip_header=False), encoding="utf-8")
    for name, spec in sec.items():
        if name in ("acts", "front_matter_pages"):
            continue
        first, last = spec["pages"]
        txt = section_text(pages_dir, index, labels_in(index, first, last), columns=spec.get("columns", False))
        (out_dir / f"{name}.txt").write_text(f"# {vol['title']} — {spec['title']}\n\n" + txt, encoding="utf-8")
        print(f"{name}.txt: pages {first}-{last}")
    plates = sorted(k for k in index if k.startswith("plate"))
    if plates:
        (out_dir / "plates.txt").write_text(
            f"# {vol['title']} — plates (facsimile signatures), OCR of the captions\n\n"
            + section_text(pages_dir, index, plates, strip_header=False), encoding="utf-8")


if __name__ == "__main__":
    main()
