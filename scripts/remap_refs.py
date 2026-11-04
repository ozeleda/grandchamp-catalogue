#!/usr/bin/env python3
"""Keep hand annotations attached to the right acts after acts.txt changes.

When a fix splits or merges acts, the refs after that point shift. This script aligns the
previous acts.txt (default: the committed version, `git show HEAD:text/vol<N>/acts.txt`)
with the current one, act by act (same page or the next one, overlapping words), and
renames the keys of annotations/vol<N>.json accordingly. Acts that are new in the current
file get no annotation; annotations whose act disappeared are listed.

Usage: python3 scripts/remap_refs.py 2 [previous_acts.txt]
"""
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import organize_text as O  # noqa: E402


def words(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {w for w in s.split() if len(w) > 3}


def align(old, new):
    A = [words(a["summary"]) for a in old]
    Bw = [words(b["summary"]) for b in new]

    def sim(i, j):
        a, b = A[i], Bw[j]
        return len(a & b) / min(len(a), len(b)) if a and b else (1.0 if old[i]["summary"] == new[j]["summary"] else 0)

    n, m = len(old), len(new)
    NEG = float("-inf")
    score = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    score[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best, bk = NEG, None
            if i and score[i - 1][j] - 0.2 > best:
                best, bk = score[i - 1][j] - 0.2, "o"
            if j and score[i][j - 1] - 0.2 > best:
                best, bk = score[i][j - 1] - 0.2, "n"
            if i and j and abs(old[i - 1]["page"] - new[j - 1]["page"]) <= 1:
                s = sim(i - 1, j - 1)
                if s >= 0.35 and score[i - 1][j - 1] + s > best:
                    best, bk = score[i - 1][j - 1] + s, "m"
            score[i][j], back[i][j] = best, bk
    i, j, pairs = n, m, []
    while i or j:
        bk = back[i][j]
        if bk == "m":
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif bk == "o":
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


def main():
    vid = sys.argv[1]
    cur_path = ROOT / "text" / f"vol{vid}" / "acts.txt"
    if len(sys.argv) > 2:
        prev_text = Path(sys.argv[2]).read_text(encoding="utf-8")
    else:
        prev_text = subprocess.run(["git", "show", f"HEAD:text/vol{vid}/acts.txt"], cwd=ROOT,
                                   capture_output=True, text=True, check=True).stdout
    tmp = ROOT / "text" / f"vol{vid}" / ".acts_prev.txt"
    tmp.write_text(prev_text, encoding="utf-8")
    old, _, _ = O.read_acts(tmp)
    tmp.unlink()
    new, _, _ = O.read_acts(cur_path)
    pairs = align(old, new)
    mapping = {old[a]["ref"]: new[b]["ref"] for a, b in pairs if a is not None and b is not None}
    ann_path = ROOT / "annotations" / f"vol{vid}.json"
    ann = json.loads(ann_path.read_text(encoding="utf-8"))
    acts, lost = {}, []
    for ref, fields in ann["acts"].items():
        if ref in mapping:
            acts[mapping[ref]] = fields
        else:
            lost.append(ref)
    changed = sum(1 for k, v in mapping.items() if k != v)
    added = [new[b]["ref"] for a, b in pairs if a is None]
    ann["acts"] = dict(sorted(acts.items(), key=lambda kv: (int(re.match(r"\d+", kv[0].rsplit("-", 1)[1]).group()), kv[0])))
    ann_path.write_text(json.dumps(ann, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"vol {vid}: {changed} refs renamed; new acts without annotation: {added}; "
          f"annotations whose act disappeared: {lost}")


if __name__ == "__main__":
    main()
