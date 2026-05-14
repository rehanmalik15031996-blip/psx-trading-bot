"""Smoke-test the new Phase F triggers and 3 new cases."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain.playbook import (
    load_cases, validate_file, retrieve_analogues, _briefing_facts, _eval_trigger,
    DEFAULT_CASES_PATH
)
_build_facts = _briefing_facts

# 1. Validate the file
n_cases, errors = validate_file(DEFAULT_CASES_PATH)
print(f"validate_file: {n_cases} cases, {len(errors)} errors")
for e in errors[:5]:
    print(f"  ERROR: {e}")

# 2. Load and confirm new cases are present
cases = load_cases()
new_ids = ["distribution_day_signature", "event_eve_distribution",
            "brent_plateau_e_and_p_decay"]
existing_ids = {c.id for c in cases}
print(f"\nLoaded {len(cases)} cases. New cases present:")
for nid in new_ids:
    print(f"  {nid}: {'OK' if nid in existing_ids else 'MISSING'}")

# 3. Build facts from a synthetic briefing simulating May 14 close
briefing_may14 = {
    "kse100_intraday": {
        "yest_close": 167451.13,
        "today_open": 168442.0,
        "today_high": 168528.87,
        "today_low":  166398.90,
        "today_close": 166498.83,
    },
    "brent_series": [104.5, 105.0, 105.2, 104.9, 105.1, 105.0],
    "policy_rate": {"policy_rate_pct": 11.0},
    "industry_kpis": {"kpis": {"kse100_ret_5d": -0.018,
                                "kse100_ret_21d": -0.030,
                                "kibor_3m_pct": 11.20}},
    "macro_snapshot": {"indicators": {
        "brent": {"last": 105.0},
        "usdpkr": {"last": 282.4},
    }},
    "regime": {"current_regime": "NORMAL"},
    "days_to_next_event": 1,
    "drivers": [{"tag": "oil_up", "magnitude": "MODERATE"}],
}
facts = _build_facts(briefing_may14)
ml = facts["macro_levels"]
print(f"\nFacts from synthetic May 14 briefing:")
print(f"  kse100_close_in_range_pct: {ml.get('kse100_close_in_range_pct')}")
print(f"  kse100_intraday_range_pct: {ml.get('kse100_intraday_range_pct')}")
print(f"  kse100_open_to_close_pct:  {ml.get('kse100_open_to_close_pct')}")
print(f"  brent_5d_slope_pct:        {ml.get('brent_5d_slope_pct')}")
print(f"  days_to_next_event:         {facts.get('days_to_next_event')}")

# 4. Test individual triggers
trigger_tests = [
    "kse100_intraday_range_gte:0.8",
    "kse100_close_in_range_lte:25",
    "kse100_open_to_close_lte:-0.3",
    "days_to_active_event_lte:2",
    "brent_5d_slope_lte:0.5",
    "brent_gte:95.0",
    "brent_5d_slope_gte:1.0",     # should be FALSE (slope ~ +0.48%)
]
print(f"\nTrigger evaluations:")
for t in trigger_tests:
    result = _eval_trigger(t, facts)
    expected = (
        True  if t == "kse100_intraday_range_gte:0.8"        else  # 1.27 >= 0.8
        True  if t == "kse100_close_in_range_lte:25"          else  # 5 <= 25
        True  if t == "kse100_open_to_close_lte:-0.3"         else  # -1.15 <= -0.3
        True  if t == "days_to_active_event_lte:2"            else  # 1 <= 2
        True  if t == "brent_5d_slope_lte:0.5"                else  # ~0.48 <= 0.5
        True  if t == "brent_gte:95.0"                        else  # 105 >= 95
        False if t == "brent_5d_slope_gte:1.0"                else  # 0.48 < 1.0
        None
    )
    ok = "OK " if result is expected else "BAD"
    print(f"  [{ok}] {t:<38} -> {result}  (expected {expected})")

# 5. Retrieve analogues for the May 14 briefing — should fire our new cases
res = retrieve_analogues(briefing_may14, top_k=10, min_score=1.0)
print(f"\nAnalogues fired (top 10):")
for r in res:
    print(f"  - {r.get('id'):<38} score={r.get('match_score'):.2f}  "
          f"triggers_fired={r.get('fired_triggers')}")

# Confirm all 3 new cases fire
fired_ids = {r.get("id") for r in res}
for nid in new_ids:
    status = "FIRED" if nid in fired_ids else "did NOT fire"
    print(f"  {nid}: {status}")
