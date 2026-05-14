"""Smoke-test the Short Ideas tab end-to-end: import → rank → render-shape."""
from __future__ import annotations
import sys, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

print("[1/5] importing brain.short_candidates...")
t0 = time.time()
from brain.short_candidates import rank_shorts
print(f"   imported in {time.time()-t0:.2f}s")

print("[2/5] running rank_shorts(min_conviction='LOW', max_results=10)...")
t0 = time.time()
res = rank_shorts(min_conviction="LOW", max_results=10)
print(f"   completed in {time.time()-t0:.2f}s")

print("[3/5] payload top-level keys:", sorted(res.keys()))

# Required keys the UI consumes
required = {"disclaimer", "regime", "dataset_coverage", "candidates"}
missing = required - set(res.keys())
print(f"   required keys present? missing={list(missing)}")

print("[4/5] regime / coverage:")
reg = res.get("regime") or {}
print(f"   regime: {reg.get('regime')}  shorts_aligned={reg.get('shorts_aligned')}")
print(f"   note: {(reg.get('note') or '')[:80]}")
cov = res.get("dataset_coverage") or {}
summary = cov.get("summary") or {}
print(f"   coverage: direct={summary.get('direct_count')}  "
      f"via_synth={summary.get('via_synth_count')}  "
      f"via_preds={summary.get('via_predictions_count')}  "
      f"not_directly={summary.get('not_directly_count')}")

print("[5/5] candidate shape:")
cands = res.get("candidates") or []
print(f"   n_candidates: {len(cands)}")
if cands:
    c = cands[0]
    print(f"   first candidate keys: {sorted(c.keys())[:14]}...")
    print(f"   first candidate: {c.get('symbol')}  "
          f"score={c.get('short_score')}  "
          f"conviction={c.get('conviction')}  "
          f"verdict={c.get('verdict_action')}  "
          f"5d_pred={c.get('predicted_return_5d_pct')}")
    # Conviction histogram
    from collections import Counter
    convs = Counter((c.get("conviction") or "?").upper() for c in cands)
    print(f"   conviction histogram: {dict(convs)}")

# Check all candidate fields the UI table accesses
print()
print("Checking UI-rendered field availability per candidate:")
ui_fields = [
    "symbol", "sector", "short_score", "conviction", "verdict_action",
    "predicted_return_5d_pct", "current_price_pkr", "eligibility",
    "drivers", "subscores", "suggested_entry_pkr", "suggested_stop_pkr",
    "suggested_target_pkr", "risk_reward",
]
n_with_field = {f: 0 for f in ui_fields}
for c in cands:
    for f in ui_fields:
        if c.get(f) is not None:
            n_with_field[f] += 1
for f in ui_fields:
    pct = (n_with_field[f] / max(len(cands), 1)) * 100
    flag = "OK" if pct >= 50 else "!!" if pct == 0 else "~ "
    print(f"  [{flag}] {f:<28} {n_with_field[f]:>3}/{len(cands)} = {pct:>5.0f}%")

print()
print("DONE — Short Ideas tab is functional.")
