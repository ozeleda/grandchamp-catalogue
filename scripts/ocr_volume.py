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
import os
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


def find_gutter(pdf, page, img_width):
    """x (pixels) of the gutter: the widest column free of words in the embedded text
    layer, searched in the middle of the spread."""
    html = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True).stdout
    pw = float(re.search(r'<page width="([\d.]+)"', html).group(1))
    occ = [0] * (int(pw) + 1)
    for m in re.finditer(r'xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)"', html):
        for x in range(int(float(m.group(1))), min(int(float(m.group(2))) + 1, len(occ))):
            occ[x] += 1
    best, run, start = (0, pw / 2), 0, 0
    for x in range(int(pw * 0.38), int(pw * 0.62)):
        if occ[x] == 0:
            run += 1
            if run > best[0]:
                best = (run, x - run / 2)
        else:
            run = 0
    return int(best[1] * img_width / pw)


def text_layer_words(pdf, page, img_width):
    """Words of the PDF's embedded OCR layer, in pixel coordinates (centre x, centre y)."""
    html = subprocess.run(["pdftotext", "-bbox", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True).stdout
    pw = float(re.search(r'<page width="([\d.]+)"', html).group(1))
    k = img_width / pw
    out = []
    for m in re.finditer(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', html):
        x0, y0, x1, y1 = (float(m.group(i)) * k for i in range(1, 5))
        t = m.group(5).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
        out.append({"x": x0, "xe": x1, "y": (y0 + y1) / 2, "text": t})
    return out


def rotate_point(x, y, angle, cx, cy):
    """Where PIL's Image.rotate(angle) (counter-clockwise, about the centre) sends (x, y)."""
    import math
    a = math.radians(angle)
    dx, dy = x - cx, y - cy
    return cx + dx * math.cos(a) + dy * math.sin(a), cy - dx * math.sin(a) + dy * math.cos(a)


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
    # One thread per Tesseract: several multi-threaded instances in parallel stall badly.
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    tsv = subprocess.run(["tesseract", str(path), "-", "-l", "fra+ita", "--psm", "6", "tsv"],
                         capture_output=True, text=True, env=env, timeout=300).stdout
    words = []
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        words.append({"block": int(f[2]), "par": int(f[3]), "line": int(f[4]),
                      "x": int(f[6]), "y": int(f[7]), "w": int(f[8]), "h": int(f[9]),
                      "conf": float(f[10]), "text": f[11]})
    return words


DATE_TOKEN = re.compile(r"^(?:[0-9iIl]{1,2}(?:er|°|\")?|[—–~-]{1,2}|[sS3]\.|d\.|[a-zéû]{3,5}\.?|1[56][0-9OoiIl]{2})$")
DATE_TEXT = re.compile(r"^(?:[0-9iIl]{1,2}(?:er|°|\")?\s+(?:[—–~-]{1,2}|[a-zéû]{3,5}\.?)|[sS3]\.\s?d\.|[1i][56][0-9OoiIl]{2})$")


def layout_text(words, page_w, layer_words=()):
    """Rebuild text lines with indentation (in characters) relative to the text block.
    layer_words: words of the PDF's own text layer (same coordinates), used to
    restore dates in the margin that Tesseract missed."""
    if not words:
        return ""
    good = [w for w in words if w["conf"] > 50 and len(w["text"]) > 2 and re.search(r"[A-Za-zÀ-ÿ]", w["text"])]
    if len(good) < 10:
        return ""
    heights = sorted(w["h"] for w in good)
    hmed = heights[len(heights) // 2]
    char_w = statistics.median(w["w"] / len(w["text"]) for w in good)
    # Text block edges from lines that are clearly text (5+ confident words);
    # anything outside is gutter or page-edge shadow.
    by_line = {}
    for w in words:
        by_line.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    firsts, ends = [], []
    for ws in by_line.values():
        conf = sorted((w for w in ws if w["conf"] > 60 and re.search(r"[a-zà-ÿ]{2}", w["text"])),
                      key=lambda w: w["x"])
        if len(conf) >= 5:
            firsts.append(conf[0]["x"])
            ends.append(conf[-1]["x"] + conf[-1]["w"])
    if not firsts:
        return ""
    firsts.sort(); ends.sort()
    text_left = firsts[int(len(firsts) * 0.1)]
    left = text_left - 16 * char_w            # the date column sits up to ~12 chars left of the text
    right = ends[int(len(ends) * 0.9)] + 4 * char_w
    datey = re.compile(r"^(?:[\[(]?\d{1,4}(?:er|°)?[\])]?|[—–-]+|[sS]\.|d\.|janv\.?|f[ée]vr?\.?|mars|avril|mai|juin|juil\.?|"
                       r"ao[uû]t|sept\.?|oct\.?|nov\.?|d[ée]c\.?|[A-Z][a-z]+|[a-zé]+\.)$")
    # group into lines by Tesseract's line ids, then order by y
    lines = {}
    for w in words:
        if w["x"] < left or w["x"] + w["w"] > right:      # gutter / edge shadow
            continue
        if w["x"] < text_left - 2 * char_w and not (w["conf"] > 40 and datey.match(w["text"])):
            continue                                       # margin zone: dates only
        if w["h"] > 3 * hmed:                              # tall specks
            continue
        lines.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    rows = []
    for ws in lines.values():
        ws.sort(key=lambda w: w["x"])
        # drop isolated low-confidence junk at either end
        junk = lambda w: w["conf"] < 30 and not re.search(r"\w{2}|\d", w["text"])
        while ws and junk(ws[0]):
            ws.pop(0)
        while ws and junk(ws[-1]):
            ws.pop()
        if ws:
            rows.append(ws)
    rows.sort(key=lambda ws: statistics.median(w["y"] for w in ws))
    # restore dates in the margin from the text layer when Tesseract has none
    margin_words = [w for w in layer_words if left <= w["x"] < text_left - 1.5 * char_w
                    and DATE_TOKEN.match(w["text"])]
    for ws in rows:
        own = " ".join(w["text"] for w in ws if w["x"] < text_left - 2 * char_w)
        if DATE_TEXT.match(own):
            continue
        yc = statistics.median(w["y"] + w["h"] / 2 for w in ws)
        cand = sorted((w for w in margin_words if abs(w["y"] - yc) < 0.6 * hmed), key=lambda w: w["x"])
        txt = " ".join(w["text"] for w in cand)
        if cand and DATE_TEXT.match(txt):
            ws[:] = [w for w in ws if w["x"] >= text_left - 2 * char_w]
            ws[:0] = [{"x": int(w["x"]), "y": int(yc), "w": int(w["xe"] - w["x"]), "h": hmed,
                       "conf": 90, "text": w["text"]} for w in cand]
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
    out_dir = ROOT / cfg["text_source"] / ("test" if args.pages else "")
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = set(cfg.get("missing_pages", []))

    def process(page):
        with tempfile.TemporaryDirectory() as tmp:
            img = render(pdf, page, tmp)
            g = find_gutter(pdf, page, img.width)
            res = []
            layer = text_layer_words(pdf, page, img.width)
            for side, box in enumerate(((0, 0, g, img.height), (g, 0, img.width, img.height))):
                half = img.crop(box)
                half, angle = deskew(half)
                cx, cy = half.width / 2, half.height / 2
                lw = []
                for w in layer:
                    if not box[0] <= w["x"] < box[2]:
                        continue
                    x, y = rotate_point(w["x"] - box[0], w["y"], angle, cx, cy)
                    xe, _ = rotate_point(w["xe"] - box[0], w["y"], angle, cx, cy)
                    lw.append({"x": x, "xe": xe, "y": y, "text": w["text"]})
                text = layout_text(ocr_words(half, tmp), half.width, lw)
                res.append({"pdf_page": page, "side": side, "chars": len(text.strip()),
                            "angle": angle, "header": page_number(text), "text": text})
            print(f"  pdf {page}: gutter {g}, {[r['chars'] for r in res]} chars, headers "
                  f"{[r['header'] for r in res]}", file=sys.stderr)
            return res

    with ThreadPoolExecutor(args.jobs) as ex:
        halves = [h for res in ex.map(process, range(first, last + 1)) for h in res]

    # drop empty halves and weak duplicates shot again alone on the next PDF page
    # skip_halves: "30R" = right half of PDF page 30 (weak duplicate of a page shot again alone)
    skip = set(two.get("skip_halves", []))
    keep = []
    for h in halves:
        if f"{h['pdf_page']}{'LR'[h['side']]}" in skip or h["chars"] < 60:
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
