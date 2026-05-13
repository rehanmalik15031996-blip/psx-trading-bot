"""One-shot inspector for predictions_log.json — date distribution + sample."""
import json, pathlib
from collections import Counter

log = json.loads(pathlib.Path("data/predictions_log.json").read_text(encoding="utf-8"))
preds = log.get("predictions", [])
print(f"Total: {len(preds)}")

asofs = Counter()
gen_dates = Counter()
for p in preds:
    if isinstance(p, dict):
        asofs[p.get("as_of_date", "?")] += 1
        gid = p.get("prediction_id", "")
        gen_dates[gid[:10]] += 1

print()
print("By as_of_date (last 6):")
for d, n in sorted(asofs.items())[-6:]:
    print(f"  {d}: {n}")
print()
print("By prediction_id date (last 6):")
for d, n in sorted(gen_dates.items())[-6:]:
    print(f"  {d}: {n}")

print()
print("Latest 5 rows by prediction_id:")
rows = sorted([p for p in preds if isinstance(p, dict)],
              key=lambda p: p.get("prediction_id", ""))[-5:]
for p in rows:
    pid = (p.get("prediction_id") or "")[:24]
    sym = p.get("symbol")
    asof = p.get("as_of_date")
    entry = p.get("entry_price_pkr")
    stop = p.get("suggested_stop_pkr")
    tgt = p.get("suggested_target_pkr")
    act = p.get("suggested_action")
    print(f"  pid={pid}  sym={sym:<8} act={act:<6} as_of={asof}  "
          f"entry={entry}  stop={stop}  target={tgt}")
