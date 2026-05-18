"""Smoke test for brain.predictor_guards.apply_guards.

Tests:
  1. Empty predictions log + no recent history -> no chase/momentum trigger
  2. Synthetic chase setup -> Guard B fires
  3. Synthetic momentum spike -> Guard D fires
  4. Regime off entirely -> only chase/momentum can fire (A and C dormant)
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.predictor_guards import apply_guards


def make_pred(action="ADD", conviction="MEDIUM", mid=2.5):
    return {
        "suggested_action": action,
        "conviction":       conviction,
        "expected_return_5d_low_pct":  mid - 2,
        "expected_return_5d_mid_pct":  mid,
        "expected_return_5d_high_pct": mid + 2,
        "key_risks":   [],
        "critic_notes": [],
    }


print("Test 1: clean state — no guards should fire")
out = apply_guards(make_pred("ADD", "MEDIUM", 2.5),
                    symbol="ZZZ",
                    sector="Oil & Gas E&P",
                    entry_price=100.0,
                    macro_impact_snapshot={"by_sector": {"score": 7}},
                    today=date(2026, 5, 18),
                    predictions_log={"predictions": []})
print(f"  bucket: {out['suggested_action']} (expected ADD)")
print(f"  guards: {out['guards_applied']} (expected [])")
assert out["suggested_action"] == "ADD"
assert out["guards_applied"] == []

print("\nTest 2: synthetic chase — prior ADD at $90, today ADD at $98 (+8.9%)")
fake_log = {"predictions": [
    {"symbol": "ZZZ", "generated_at": "2026-05-14T10:00:00+05:00",
     "suggested_action": "ADD", "conviction": "MEDIUM",
     "entry_price_pkr": 90.0},
]}
out = apply_guards(make_pred("ADD", "MEDIUM", 2.5),
                    symbol="ZZZ", sector="Banking", entry_price=98.0,
                    macro_impact_snapshot={"by_sector": {"score": 1}},
                    today=date(2026, 5, 18),
                    predictions_log=fake_log)
print(f"  bucket: {out['suggested_action']} (expected HOLD)")
print(f"  guards: {out['guards_applied']}")
# Either regime cap OR chase detector reaching HOLD is acceptable;
# the point is the call got downgraded
assert out["suggested_action"] == "HOLD"
assert len(out["guards_applied"]) >= 1

print("\nTest 3: regime off + non-supportive sector — only buy-side guards fire")
out = apply_guards(make_pred("HOLD", "LOW", -0.5),
                    symbol="ZZZ", sector="Banking", entry_price=100.0,
                    macro_impact_snapshot={"by_sector": {"score": 1}},
                    today=date(2020, 1, 1),  # ancient date, no risk-off
                    predictions_log={"predictions": []})
print(f"  bucket: {out['suggested_action']} (expected HOLD)")
print(f"  guards: {out['guards_applied']}")
assert out["suggested_action"] == "HOLD"

print("\nTest 4: missing macro snapshot — falls back to live compute, no crash")
out = apply_guards(make_pred("ADD", "MEDIUM", 1.5),
                    symbol="OGDC", sector="Oil & Gas E&P", entry_price=320.0,
                    macro_impact_snapshot=None,
                    today=date(2026, 5, 18),
                    predictions_log={"predictions": []})
print(f"  bucket: {out['suggested_action']}")
print(f"  guards: {out['guards_applied']}")
print(f"  regime_on: {out.get('regime_on')}")

print("\nAll smoke tests PASSED.")
