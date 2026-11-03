#!/usr/bin/env python3
"""OCR a photocopy scan with two book pages per PDF page (volume 4) with Tesseract.

Each PDF page of the parts listed in volumes.json ("scans", with "two_up": true) is rendered
at 300 dpi, split at the gutter, deskewed, and each half is OCR'd with Tesseract (French +
Italian). The layout text (margin dates at the left, text indented) is cached in
text/vol<N>/halves/<part>-<pdf page><L|R>.txt; extract_pages.py then numbers the pages from
their running headers, exactly as for the single-page scans.

Usage: python3 scripts/ocr_two_up.py 4 [--jobs 4]
"""
import argparse
import json
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ocr_volume as O  # noqa: E402


def ink_gutter(img):
    """x of the gutter from the image: the column with least ink in the middle of the spread."""
    prof, k = O.ink_profile(img, 0)
    lo, hi = int(len(prof) * 0.40), int(len(prof) * 0.60)
    xs = range(lo, hi)
    # smooth over a few columns so that a single clean column between letters does not win
    best = min(xs, key=lambda x: sum(prof[max(0, x - 6):x + 7]))
    return best * k


def process(pdf, page, out_dir):
    with tempfile.TemporaryDirectory() as tmp:
        img = O.render(pdf, page, tmp)
        try:
            g = O.find_gutter(pdf, page, img.width)
        except Exception:
            g = None
        if not g or not 0.4 * img.width < g < 0.6 * img.width:
            g = ink_gutter(img)
        for side, box in (("L", (0, 0, g, img.height)), ("R", (g, 0, img.width, img.height))):
            half, angle = O.deskew(img.crop(box))
            text = O.layout_text(O.ocr_words(half, tmp), half.width)
            (out_dir / f"{Path(pdf).stem.rsplit('_', 1)[-1]}-{page:03d}{side}.txt").write_text(text, encoding="utf-8")
        return page, g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("volume", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    vol = next(v for v in json.loads((ROOT / "volumes.json").read_text())["volumes"] if v["id"] == args.volume)
    out_dir = ROOT / "text" / f"vol{args.volume}" / "halves"
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for name in vol["scans"]:
        pdf = ROOT / name
        part = Path(name).stem.rsplit("_", 1)[-1]
        for p in range(1, O_pages(pdf) + 1):
            if not (out_dir / f"{part}-{p:03d}R.txt").exists():
                jobs.append((pdf, p))
    with ProcessPoolExecutor(args.jobs) as ex:      # processes: deskew is pure Python
        for page, g in ex.map(process, [j[0] for j in jobs], [j[1] for j in jobs], [out_dir] * len(jobs)):
            pass
    print(f"vol {args.volume}: {len(jobs)} PDF pages OCR'd into {out_dir}", file=sys.stderr)


def O_pages(pdf):
    import re
    import subprocess
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1))


if __name__ == "__main__":
    main()
