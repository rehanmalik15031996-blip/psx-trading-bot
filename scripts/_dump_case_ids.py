"""List every distinct fired-case id we have history on."""
import json, sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

per_date = json.loads((ROOT/"data/_research/backtest_per_date.json").read_text(encoding="utf-8"))
ctr = Counter()
for r in per_date:
    for f in (r.get("fired") or []):
        if isinstance(f, dict):
            ctr[f.get("id") or "?"] += 1
        else:
            ctr[str(f)] += 1
print("Distinct fired case ids in backtest_per_date (with counts):")
for cid, n in sorted(ctr.items(), key=lambda x: -x[1]):
    print(f"  {n:>4}  {cid}")

print("\n\nDistinct case ids in cases.json:")
cases = json.loads((ROOT/"data/playbook/cases.json").read_text(encoding="utf-8"))
if isinstance(cases, dict):
    cases = cases.get("cases", [])
for c in cases:
    cid = (c.get("id") or c.get("case_id") or "?") if isinstance(c, dict) else "?"
    print(f"  {cid}")

print("\n\nSector_moves_catalog top-level shape:")
cat = json.loads((ROOT/"data/_research/sector_moves_catalog.json").read_text(encoding="utf-8"))
print(f"  keys: {list(cat.keys())[:10]}")
for k in list(cat.keys())[:3]:
    v = cat[k]
    if isinstance(v, dict):
        print(f"  cat[{k}] keys: {list(v.keys())[:10]}")
    elif isinstance(v, list):
        print(f"  cat[{k}] list len={len(v)} first={v[0] if v else None}")
