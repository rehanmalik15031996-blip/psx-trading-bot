"""Did Monday's briefing have playbook analogues firing?"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
b = json.loads((ROOT/"data/_strategist/_briefing_2026-05-12.json").read_text(encoding="utf-8"))

pb = b.get("playbook_analogues") or {}
pf = b.get("playbook_facts") or {}

print("=== playbook_analogues in May 12 briefing ===")
print(f"  type: {type(pb).__name__}")
if isinstance(pb, dict):
    print(f"  keys: {list(pb.keys())[:20]}")
    for k, v in pb.items():
        if isinstance(v, dict):
            score = v.get("match_score")
            fired = v.get("fired_triggers")
            conf = v.get("confidence")
            print(f"  - {k}: score={score} conf={conf} fired={fired}")
        else:
            print(f"  - {k}: ({type(v).__name__}) {str(v)[:60]}")
elif isinstance(pb, list):
    print(f"  list len: {len(pb)}")
    for x in pb[:5]:
        if isinstance(x, dict):
            print(f"  - {x.get('id')}: score={x.get('match_score')} fired={x.get('fired_triggers')}")

print()
print("=== playbook_facts (input to matcher) ===")
print(f"  keys: {list(pf.keys())[:30]}")
for k in ["active_events", "breadth_pct_up", "market_regime",
          "universe_5d_ret", "sentiment_tilt", "fipi_5d_net_pkr_mn",
          "market_risk_on", "drivers_strong", "drivers_all"]:
    if k in pf:
        v = pf[k]
        if isinstance(v, (list, set, tuple)) and len(str(v)) > 200:
            print(f"  {k}: ({len(v)}) sample: {list(v)[:5]}")
        else:
            print(f"  {k}: {v}")
