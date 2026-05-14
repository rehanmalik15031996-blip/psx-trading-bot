"""Quick dump of a backtest row to understand the schema."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
import sys
sys.stdout.reconfigure(encoding="utf-8")

pd_data = json.loads((ROOT/"data/_research/backtest_per_date.json").read_text(encoding="utf-8"))
print(f"rows: {len(pd_data)}")

for idx in (0, 100, 150, 200, 250):
    if idx >= len(pd_data):
        continue
    r = pd_data[idx]
    print(f"\n--- row idx={idx} as_of={r.get('as_of')} ---")
    for k, v in r.items():
        if isinstance(v, list):
            print(f"  {k}: list len={len(v)}")
            if v:
                print(f"    first item type={type(v[0]).__name__}  value={v[0]!r}"[:200])
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())[:8]}")
        else:
            print(f"  {k}: {v}")
