"""Gap-2 follow-up: conditional analysis of the overlay's value.

The flat aggregate edge (+0.04pp / +0.02pp) over 5y could mean either:
  (a) the playbook is useless, OR
  (b) the playbook correctly forgoes upside in normal weeks and
      compensates by saving losses in bad weeks — which is exactly
      what a defensive system SHOULD do.

To distinguish, bucket the 258 backtest dates by the *forward universe
return* and check the overlay edge in each.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import statistics as st

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

rows = json.loads((ROOT / "data" / "_research" /
                    "backtest_per_date.json").read_text(encoding="utf-8"))


def bucket_by_univ(r: dict) -> str:
    u = r.get("univ_ret_5d_pct")
    if u is None:
        return "?"
    if u <= -3.0:
        return "crash_le_-3"
    if u <= -1.0:
        return "down_-3_to_-1"
    if u <= +1.0:
        return "flat_-1_to_+1"
    if u <= +3.0:
        return "up_+1_to_+3"
    return "rally_ge_+3"


by = {}
for r in rows:
    b = bucket_by_univ(r)
    by.setdefault(b, []).append(r)

print(f"{'forward universe 5d bucket':<22} {'n':>4} "
      f"{'univ%':>8} {'base%':>8} {'over%':>8} "
      f"{'edge_vs_base':>13} {'overlay_wins':>13}")
print("-" * 88)
order = ["crash_le_-3", "down_-3_to_-1", "flat_-1_to_+1",
         "up_+1_to_+3", "rally_ge_+3"]
summary = {}
for b in order:
    rs = by.get(b, [])
    if not rs:
        continue
    univ = [r["univ_ret_5d_pct"]      for r in rs]
    base = [r["pnl_baseline_5d_pct"]  for r in rs]
    over = [r["pnl_overlay_5d_pct"]   for r in rs]
    edge = [o - bv for o, bv in zip(over, base)]
    wins = sum(1 for e in edge if e > 0)
    summary[b] = {
        "n": len(rs),
        "univ_mean":  st.mean(univ),
        "base_mean":  st.mean(base),
        "over_mean":  st.mean(over),
        "edge_mean":  st.mean(edge),
        "win_rate":   wins / len(edge),
    }
    s = summary[b]
    print(f"{b:<22} {s['n']:>4} "
          f"{s['univ_mean']:>+7.2f}% {s['base_mean']:>+7.2f}% "
          f"{s['over_mean']:>+7.2f}% {s['edge_mean']:>+12.2f}pp "
          f"{s['win_rate']*100:>11.1f}%")

print()
crash = summary.get("crash_le_-3", {})
down  = summary.get("down_-3_to_-1", {})
flat  = summary.get("flat_-1_to_+1", {})
rally = summary.get("rally_ge_+3", {})

print("=" * 88)
print("VERDICT (conditional on what the universe actually did)")
print("=" * 88)
crash_edge = crash.get("edge_mean", 0)
crash_n    = crash.get("n", 0)
rally_edge = rally.get("edge_mean", 0)
rally_n    = rally.get("n", 0)
flat_edge  = flat.get("edge_mean", 0)
flat_n     = flat.get("n", 0)

print(f"Crash weeks    (universe <= -3%):  n={crash_n}, "
      f"overlay edge = {crash_edge:+.2f}pp")
print(f"Down weeks     (-3% .. -1%):       n={down.get('n',0)}, "
      f"overlay edge = {down.get('edge_mean',0):+.2f}pp")
print(f"Flat weeks     (-1% .. +1%):       n={flat_n}, "
      f"overlay edge = {flat_edge:+.2f}pp")
print(f"Up weeks       (+1% .. +3%):       n={by.get('up_+1_to_+3',{}).__len__() if isinstance(by.get('up_+1_to_+3',{}), list) else summary.get('up_+1_to_+3',{}).get('n',0)}, "
      f"overlay edge = {summary.get('up_+1_to_+3',{}).get('edge_mean',0):+.2f}pp")
print(f"Rally weeks    (>= +3%):           n={rally_n}, "
      f"overlay edge = {rally_edge:+.2f}pp")
print()

if crash_n >= 5 and crash_edge >= 0.5:
    print("[MEANINGFUL DEFENSIVE VALUE] Overlay clearly saves money during "
          "crash weeks (the regime it's designed for).")
elif crash_n >= 5 and crash_edge < 0:
    print("[CRITICAL FAIL] Overlay LOSES money during crash weeks too — "
          "the defensive system is broken in its target regime.")
elif crash_n < 5:
    print("[INCONCLUSIVE] Too few crash weeks in the sample to evaluate "
          "the defensive value of the overlay.")
else:
    print("[WEAK] Overlay saves only a small amount during crashes — "
          "marginal defensive value.")

if rally_n >= 5 and rally_edge < -1.0:
    print(f"[OPPORTUNITY COST] Overlay forgoes {-rally_edge:.2f}pp on "
          "rally weeks. That's the cost of being defensive — acceptable "
          "if crash protection is strong, expensive otherwise.")

out = ROOT / "data" / "_research" / "backtest_conditional_by_universe.json"
out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(f"\nWrote {out}")
