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


def normalise(text):
    out = []
    for l in text.split("\n"):
        l = l.rstrip()
        l = re.sub(r"^(\s{0,6}(?:\S{1,3}\s+)?[a-zéû]{2,6}) \.(?=\s)", r"\1.", l)               # margin "oct ." -> "oct."
        l = re.sub(r"[\u0590-\u08ff\u200e\u200f\u202a-\u202e]+", "", l)   # stray Arabic/bidi glyphs
        l = re.sub(r"^(\s*1[56]\d\d)\s?\.\s*$", r"\1", l)              # year heading "1606 ."
        l = re.sub(r"^(\s{0,3})s\. d \.", r"\1s. d.", l)
        l = re.sub(r"^[-.]?(\d{1,2})[.]?(?=\s{3,}\S)", r"\1", l)           # margin "26." / "-16" / ".27"
        l = re.sub(r"^(\S.{0,14}?\s{2,})\. (?=En )", r"\1", l)            # "15     . En italien"
        l = re.sub(r"^(\d{1,2}(?:er)?(?:\s+[a-zéû]{2,6}\.?)?\s{3,})(\d{3,4})(\s+[A-Z])", r"\1(\2)\3", l)
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
    entry = re.compile(r"^(\s{0,3}(?:1er|\d{1,2})(?:\s+[a-zéû]{2,6}\.?)?(?:\s*[—–-])?)(\s{3,})(?=\S)")
    lang_only = re.compile(r"^\s*En \w+\. —\s*$")
    for i, l in enumerate(merged):
        m = entry.match(l)
        if not m:
            continue
        rest = l[m.end():]
        nxt_i = next((k for k in range(i + 1, min(i + 4, len(merged))) if merged[k].strip()), None)
        # "27 sept.   Stefano di Burgio..." / "   En italien. —" : the language note was split off
        if nxt_i is not None and lang_only.match(merged[nxt_i]) and not rest.startswith("En "):
            k2 = next((k for k in range(nxt_i + 1, min(nxt_i + 3, len(merged))) if merged[k].strip()), None)
            if k2 is not None and re.match(r"^\s+[a-zà-ÿ]", merged[k2]):
                merged[i] = l[:m.end()] + merged[nxt_i].strip() + " " + rest
                merged[nxt_i] = ""
                continue
        # "18   En italien. —" / "   faire venir..." (end of the previous act) / "      Ettor Cuigliandro...":
        if lang_only.match(rest) and nxt_i is not None and re.match(r"^\s+[a-zà-ÿ]", merged[nxt_i]):
            k2 = next((k for k in range(nxt_i + 1, min(nxt_i + 3, len(merged))) if merged[k].strip()), None)
            if k2 is not None and re.match(r"^\s{14,}[A-ZÉ]", merged[k2]):
                cont = merged[nxt_i]
                merged[nxt_i] = l[:m.end()] + rest.strip() + " " + merged[k2].strip()
                merged[i] = cont
                merged[k2] = ""
                continue
        if not re.match(r"[a-zà-ÿ,;\d]", rest) and (rest.startswith("En ") or not nxt_i
                                                    or not re.match(r"^\s{6,}En \w+\. — ", merged[nxt_i])):
            continue
        for j in range(i + 1, min(i + 6, len(merged))):
            nxt = merged[j]
            if re.match(r"^\s{6,}En \w+\. — ", nxt):
                tok = m.group(1).strip()
                merged[j] = tok + " " * max(4, 12 - len(tok)) + nxt.strip()
                merged[i] = " " * len(m.group(0)) + l[m.end():]
                break
            if nxt.strip() and not re.match(r"^\s{6,}", nxt):
                break
    return "\n".join(merged)


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
    out_dir = ROOT / "text" / f"vol{vid}" / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.txt"):
        f.unlink()

    seq = []                               # (file, pdf_page, text, detected number)
    for name in scans:
        pdf = ROOT / name
        for p in range(1, pdf_pages(pdf) + 1):
            t = layout_text(pdf, p)
            seq.append([name, p, t, header_number(t)])

    # Anchors: pages whose header number fits an increasing run (longest chain where page
    # numbers never grow faster than PDF pages). Pages between anchors are numbered in
    # order; surplus pages between two anchors are plates (the ones with least text).
    first_body = vol["first_body_page"]                 # [part index, pdf page] of printed page 1
    start = next(i for i, s in enumerate(seq) if s[0] == scans[first_body[0]] and s[1] == first_body[1])
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
                    labels[k] = f"plate-{seq[k][1]:03d}" if seq[k][0] == scans[0] else f"plate-{scans.index(seq[k][0]) + 1}-{seq[k][1]:03d}"
                    print(f"  plate: {seq[k][0]} p.{seq[k][1]}", file=sys.stderr)
            between = keep
        elif len(between) < need:
            print(f"  ! {need - len(between)} page(s) missing between {n1} and {n2}", file=sys.stderr)
        for j, k in enumerate(between):
            labels[k] = f"p{n1 + 1 + j:03d}"
    fm = 0
    for i in range(start):
        if len(seq[i][2].strip()) >= 40:
            fm += 1
            labels[i] = f"fm{fm:02d}"

    fixes_path = ROOT / "text" / f"vol{vid}" / "fixes.json"
    page_fixes = json.loads(fixes_path.read_text(encoding="utf-8")).get("pages", []) if fixes_path.exists() else []
    index = {}
    for (name, p, t, n), lab in zip(seq, labels):
        if not lab or len(t.strip()) < 40 or re.search(r"CIRCULATION DEPARTMENT|GENERAL LIBRARY - U\.C", t):
            continue
        if lab in vol.get("second_ocr_pages", []):
            # scrambled in this scan (curved page): use the other copy's OCR instead
            t = clean_second((ROOT / vol["text_source"] / f"{lab}.txt").read_text(encoding="utf-8"))
        hand = ROOT / "text" / f"vol{vid}" / "transcribed" / f"{lab}.txt"
        if hand.exists():
            # pages the OCR scrambled, transcribed by hand from the page image
            t = hand.read_text(encoding="utf-8")
        t = normalise(t)
        for f in page_fixes:          # hand corrections of the page text, checked against the scan
            if f["page"] == lab:
                if f["old"] not in t:
                    print(f"  ! fixes.json: {f['old'][:40]!r} not found on {lab}", file=sys.stderr)
                t = t.replace(f["old"], f["new"])
        (out_dir / f"{lab}.txt").write_text(t, encoding="utf-8")
        index[lab] = {"file": name, "pdf_page": p}
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"vol {vid}: {len(index)} pages written to {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
