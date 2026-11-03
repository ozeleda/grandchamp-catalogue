#!/usr/bin/env python3
"""Read the month headings and margin dates of volumes 5-8 from the page images.

In volumes 5-8 the month is printed as a heading (and in the running head at the top of
the page), and only the day in the margin. The Google OCR of the HathiTrust scans often
drops those short lines. This script OCRs the left part of each analysis page (and the
running head) with Tesseract and keeps, for each page, the lines that are a month name or
start with a day, with their height on the page (0 = top, 1 = bottom). extract_pages.py uses
them to restore the lost month headings.

Output (cached, so Tesseract runs once): text/vol<N>/margin_ocr.json
    {"p156": {"head": "décembre", "lines": [[0.08, "29 — (105) Le", 0.11], [0.71, "Janvier", 0.12], ...]}}
    (height on the page, start of the line, left edge as a fraction of the page width)

Usage: python3 scripts/margin_ocr.py 5
"""
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
          "octobre", "novembre", "décembre"]


def month_of(word):
    w = re.sub(r"[^a-zéèûô]", "", word.lower())
    if len(w) < 3:
        return None
    hit = difflib.get_close_matches(w, MONTHS, n=1, cutoff=0.7)
    return hit[0] if hit else None


def tsv_lines(img, psm="4"):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        try:
            out = subprocess.run(["tesseract", f.name, "-", "--psm", psm, "-l", "fra", "tsv"],
                                 capture_output=True, text=True, timeout=40,
                                 env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
        except subprocess.TimeoutExpired:
            return []
    lines = {}
    for row in out.split("\n")[1:]:
        c = row.split("\t")
        if len(c) < 12 or not c[11].strip():
            continue
        key = (c[2], c[3], c[4])
        y = int(c[7]) + int(c[9]) / 2
        e = lines.setdefault(key, [y, int(c[6]), 0, []])
        e[2] = max(e[2], int(c[6]) + int(c[8]))
        e[3].append(c[11])
    return sorted((y, x0, x1, " ".join(ws)) for y, x0, x1, ws in lines.values())


def read_page(pdf, page):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "200", "-f", str(page), "-l", str(page), "-png", "-singlefile",
                        str(pdf), f"{tmp}/p"], check=True)
        im = Image.open(f"{tmp}/p.png").convert("L")
        W, H = im.size
        strip = im.crop((0, 0, int(W * 0.40), H))
        head = im.crop((0, 0, W, int(H * 0.09)))
        res = {"head": None, "lines": []}
        for y, x0, x1, t in tsv_lines(head, "6"):
            m = month_of(t.split()[0]) if t.split() else None
            if m and t.split()[0][:1].isupper():
                res["head"] = m
                break
        sw = strip.size[0]
        for y, x0, x1, t in tsv_lines(strip):
            first = t.split()[0]
            heading = (month_of(first) and first[:1].isupper() and len(t.split()) <= 2
                       and x1 < sw - 4)          # a word cut by the strip's edge is text (« Mars[eille] »)
            if heading or re.match(r"^(1er|1°|\d{1,2}|\?|s\.)\b", t):
                res["lines"].append([round(y / H, 3), t[:24], round(x0 / W, 3)])
        return res


def main():
    vid = int(sys.argv[1])
    vol = next(v for v in json.loads((ROOT / "volumes.json").read_text())["volumes"] if v["id"] == vid)
    index = json.loads((ROOT / "text" / f"vol{vid}" / "pages" / "index.json").read_text())
    out_path = ROOT / "text" / f"vol{vid}" / "margin_ocr.json"
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    a, b = vol["analysis_pages"]
    todo = [f"p{p:03d}" for p in range(a, b + 1) if f"p{p:03d}" in index and f"p{p:03d}" not in done]

    def run(lab):
        return lab, read_page(ROOT / index[lab]["file"], index[lab]["pdf_page"])

    def save():
        out_path.write_text(json.dumps(dict(sorted(done.items())), ensure_ascii=False, indent=0) + "\n",
                            encoding="utf-8")
    with ThreadPoolExecutor(4) as ex:
        for k, (lab, res) in enumerate(ex.map(run, todo)):
            done[lab] = res
            if k % 25 == 24:
                save()
    save()
    print(f"vol {vid}: {len(todo)} pages read, {len(done)} in {out_path.name}")


if __name__ == "__main__":
    main()
