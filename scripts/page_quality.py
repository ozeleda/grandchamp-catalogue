#!/usr/bin/env python3
"""Score how scrambled each page of text/vol<N>/pages is (Google OCR of curved pages
breaks lines into scattered words). Usage: python3 scripts/page_quality.py 2 [first last]"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def score(text):
    lines = [l for l in text.split("\n") if l.strip()]
    punct = sum(1 for l in lines if re.fullmatch(r"\s*[\W\d_]{1,4}\s*", l))
    gaps = sum(1 for l in lines if re.search(r"\S\s{25,}\S", l))
    short = sum(1 for l in lines if len(l.strip()) < 15)
    return punct + gaps + short, len(lines)


if __name__ == "__main__":
    vid = int(sys.argv[1])
    lo, hi = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1, 9999)
    rows = []
    for f in sorted((ROOT / "text" / f"vol{vid}" / "pages").glob("p[0-9]*.txt")):
        n = int(f.stem[1:])
        if lo <= n <= hi:
            s, nl = score(f.read_text(encoding="utf-8"))
            rows.append((s, f.stem, nl))
    for r in sorted(rows, reverse=True)[:int(sys.argv[4]) if len(sys.argv) > 4 else 40]:
        print(*r)
