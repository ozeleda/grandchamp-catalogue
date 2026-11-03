#!/usr/bin/env python3
"""Merge hand corrections into annotations/vol<N>.json.

Reads a JSON object from stdin: {"<ref>": {"<field or short code>": value, ...}, ...}
plus an optional "_reviewed_through_seq": N. Short codes:
  t=transaction_type  p1/p1o/p1r = party1 name/origins/role  p2/p2o/p2r = party2 ...
  o/oo/or = other parties names/origins/role  m=matter  pr=price  c=currency
  eq=other_equivalent  k=keywords  d=decision  n=notes
A value of null clears the field.
Usage: python3 scripts/annotate.py 1 < corrections.json
"""
import json
import sys
from pathlib import Path

SHORT = {"t": "transaction_type", "p1": "party1_name", "p1o": "party1_origins", "p1r": "party1_role",
         "p2": "party2_name", "p2o": "party2_origins", "p2r": "party2_role",
         "o": "other_parties_names", "oo": "other_parties_origins", "or": "other_parties_role",
         "m": "matter", "pr": "price", "c": "currency", "eq": "other_equivalent",
         "k": "keywords", "d": "decision", "n": "notes"}

vol = sys.argv[1]
path = Path(__file__).resolve().parent.parent / "annotations" / f"vol{vol}.json"
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"reviewed_through_seq": 0, "acts": {}}
new = json.load(sys.stdin)
upto = new.pop("_reviewed_through_seq", None)
for ref, fields in new.items():
    tgt = data["acts"].setdefault(ref, {})
    for k, v in fields.items():
        tgt[SHORT.get(k, k)] = v
if upto:
    data["reviewed_through_seq"] = max(data["reviewed_through_seq"], upto)
data["acts"] = dict(sorted(data["acts"].items(), key=lambda kv: int(kv[0].rsplit("-", 1)[1])))
path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"{path.name}: {len(data['acts'])} acts corrected, reviewed through seq {data['reviewed_through_seq']}")
