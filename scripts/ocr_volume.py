#!/usr/bin/env python3
"""Re-OCR a two-up photocopy scan (two book pages per PDF page) with Tesseract.

Some volumes are photocopier scans whose embedded OCR is too poor to parse
(skewed pages, lines split in two). This script:

  1. renders each PDF page, finds the gutter and splits it into two book pages;
  2. drops near-empty halves and weak duplicates (pages with a facsimile signature
     were shot twice: once in the spread, once alone);
  3. numbers the book pages (``missing_pages`` in volumes.json lists pages absent
     from the scan);
  4. deskews each page, OCRs it with Tesseract (French + Italian) and rebuilds a
     plain-text layout in which indentation is meaningful, like ``pdftotext -layout``;
  5. writes one text file per printed page to ``ocr/vol<N>/p<page>.txt``.

build_db.py reads those files when a volume has ``"text_source": "ocr/vol<N>"``,
so Tesseract is only needed to (re)create the cache.

Usage: python3 scripts/ocr_volume.py <volume id> [--pages 8-20] [--jobs 4]
Requires: pdftoppm (poppler), tesseract with fra and ita data, Pillow.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
DPI = 300


def render(pdf, page, out_dir):
    base = Path(out_dir) / f"r{page}"
    subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(DPI), "-gray",
                    "-png", "-singlefile", str(pdf), str(base)], check=True)
    return Image.open(f"{base}.png").convert("L")


def ink_profile(img, axis):
    """Fraction of dark pixels per column (axis=0) or row (axis=1), on a small copy."""
    small = img.resize((img.width // 4, img.height // 4))
    w, h = small.size
    px = small.load()
    if axis == 0:
        return [sum(1 for y in range(h) if px[x, y] < 110) / h for x in range(w)], 4
    return [sum(1 for x in range(w) if px[x, y] < 110) / w for y in range(h)], 4


def find_gutter(img):
    """x of the gutter: the lowest-ink column band in the middle third of the spread."""
    prof, k = ink_profile(img, 0)
    n = len(prof)
    lo, hi = int(n * 0.38), int(n * 0.62)
    win = 6
    best = min(range(lo, hi - win), key=lambda x: sum(prof[x:x + win]))
    return (best + win // 2) * k


def deskew(img):
    """Rotate by the small angle that makes text lines most horizontal."""
    small = ImageOps.invert(img.resize((img.width // 5, img.height // 5)))
    best, best_score = 0.0, -1
    for a10 in range(-30, 31, 3):
        a = a10 / 10
        r = small.rotate(a, fillcolor=0)
        w, h = r.size
        px = r.load()
        rows = [sum(px[x, y] for x in range(0, w, 2)) for y in range(h)]
        score = statistics.pvariance(rows)
        if score > best_score:
            best, best_score = a, score
    return img.rotate(best, fillcolor=255, expand=False), best


def ocr_words(img, tmp):
    path = Path(tmp) / f"h{id(img)}.png"
    img.save(path)
    tsv = subprocess.run(["tesseract", str(path), "-", "-l", "fra+ita", "--psm", "6", "tsv"],
                         capture_output=True, text=True).stdout
    words = []
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        words.append({"block": int(f[2]), "par": int(f[3]), "line": int(f[4]),
                      "x": int(f[6]), "y": int(f[7]), "w": int(f[8]), "h": int(f[9]),
                      "conf": float(f[10]), "text": f[11]})
    return words


def layout_text(words, page_w):
    """Rebuild text lines with indentation (in characters) relative to the text block."""
    if not words:
        return ""
    good = [w for w in words if w["conf"] > 50 and len(w["text"]) > 2 and re.search(r"[A-Za-zÀ-ÿ]", w["text"])]
    if len(good) < 10:
        return ""
    heights = sorted(w["h"] for w in good)
    hmed = heights[len(heights) // 2]
    char_w = statistics.median(w["w"] / len(w["text"]) for w in good)
    xs = sorted(w["x"] for w in good)
    xe = sorted(w["x"] + w["w"] for w in good)
    left, right = xs[int(len(xs) * 0.02)] - 3 * char_w, xe[int(len(xe) * 0.98)] + 3 * char_w
    # group into lines by Tesseract's line ids, then order by y
    lines = {}
    for w in words:
        if w["x"] + w["w"] < left or w["x"] > right:      # gutter / edge shadow
            continue
        if w["h"] > 3 * hmed:                              # tall specks
            continue
        lines.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    rows = []
    for ws in lines.values():
        ws.sort(key=lambda w: w["x"])
        # drop isolated low-confidence junk at either end
        while ws and ws[0]["conf"] < 30 and not re.search(r"\w{2}", ws[0]["text"]):
            ws.pop(0)
        while ws and ws[-1]["conf"] < 30 and not re.search(r"\w{2}", ws[-1]["text"]):
            ws.pop()
        if ws:
            rows.append(ws)
    rows.sort(key=lambda ws: statistics.median(w["y"] for w in ws))
    # the left margin is the date column: smallest typical line start
    starts = sorted(ws[0]["x"] for ws in rows)
    margin = starts[int(len(starts) * 0.05)] if starts else left
    out = []
    for ws in rows:
        line = ""
        for w in ws:
            col = max(0, round((w["x"] - margin) / char_w))
            if len(line) < col:
                line += " " * (col - len(line))
            elif line:
                line += " "
            line += w["text"]
        out.append(line.rstrip())
    return "\n".join(out) + "\n"


def page_number(text):
    for line in text.splitlines()[:3]:
        m = re.search(r"[-—–~_]\s*(\d{1,3})\s*[-—–~_]", line)
        if m:
            return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("volume", type=int)
    ap.add_argument("--pages", help="PDF page range to process, e.g. 8-20 (default: from volumes.json)")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    cfg = next(v for v in json.loads((ROOT / "volumes.json").read_text())["volumes"] if v["id"] == args.volume)
    two = cfg["two_up"]
    first, last = (map(int, args.pages.split("-")) if args.pages else two["pdf_pages"])
    pdf = ROOT / cfg["file"]
    out_dir = ROOT / cfg["text_source"]
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = set(cfg.get("missing_pages", []))

    def process(page):
        with tempfile.TemporaryDirectory() as tmp:
            img = render(pdf, page, tmp)
            g = find_gutter(img)
            res = []
            for side, box in enumerate(((0, 0, g, img.height), (g, 0, img.width, img.height))):
                half = img.crop(box)
                half, angle = deskew(half)
                text = layout_text(ocr_words(half, tmp), half.width)
                res.append({"pdf_page": page, "side": side, "chars": len(text.strip()),
                            "angle": angle, "header": page_number(text), "text": text})
            print(f"  pdf {page}: gutter {g}, {[r['chars'] for r in res]} chars, headers "
                  f"{[r['header'] for r in res]}", file=sys.stderr)
            return res

    with ThreadPoolExecutor(args.jobs) as ex:
        halves = [h for res in ex.map(process, range(first, last + 1)) for h in res]

    # drop empty halves and weak duplicates shot again alone on the next PDF page
    keep = []
    for i, h in enumerate(halves):
        if h["chars"] < 60:
            continue
        if h["chars"] < 450 and i + 1 < len(halves) and halves[i + 1]["chars"] < 60:
            continue
        keep.append(h)
    expected = two["first_printed_page"] if not args.pages else None
    index = {}
    for h in keep:
        if expected is None:
            expected = h["header"] or 0
        while expected in missing:
            expected += 1
        if h["header"] and h["header"] != expected and abs(h["header"] - expected) <= 4:
            print(f"  ! pdf {h['pdf_page']} side {h['side']}: header {h['header']}, expected {expected}",
                  file=sys.stderr)
        (out_dir / f"p{expected:03d}.txt").write_text(h["text"], encoding="utf-8")
        index[expected] = {"pdf_page": h["pdf_page"], "side": h["side"], "deskew": h["angle"]}
        expected += 1
    idx_path = out_dir / "index.json"
    old = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    old.update({str(k): v for k, v in index.items()})
    idx_path.write_text(json.dumps(dict(sorted(old.items(), key=lambda kv: int(kv[0]))), indent=1))
    print(f"wrote {len(index)} pages to {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
