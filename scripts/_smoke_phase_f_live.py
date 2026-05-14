"""End-to-end smoke test: Phase F helpers + playbook on LIVE briefing data."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain.master_strategist import (
    _kse100_intraday_facts, _brent_recent_series, _days_to_next_event
)
from brain.playbook import retrieve_analogues, summarise_facts

print("=" * 70)
print("PHASE F LIVE DATA SMOKE TEST")
print("=" * 70)

intra = _kse100_intraday_facts()
print(f"\n_kse100_intraday_facts:")
for k, v in intra.items():
    print(f"  {k}: {v}")

brent = _brent_recent_series(n=6)
print(f"\n_brent_recent_series (last 6):")
for i, v in enumerate(brent):
    print(f"  {i+1}. {v:.2f}")

dne = _days_to_next_event()
print(f"\n_days_to_next_event: {dne}")

# Construct a minimal briefing and try retrieving analogues
briefing = {
    "kse100_intraday":  intra,
    "brent_series":     brent,
    "days_to_next_event": dne,
    "regime":           {"current_regime": "NORMAL"},
    "policy_rate":      {"policy_rate_pct": 11.0},
    "industry_kpis":    {"kpis": {"kse100_ret_5d": -0.018,
                                    "kse100_ret_21d": -0.030}},
    "macro_snapshot":   {"indicators": {"brent": {"last": brent[-1] if brent else 105.0}}},
    "drivers":          [],
}
res = retrieve_analogues(briefing, top_k=15, min_score=1.0)
print(f"\nAnalogues fired ({len(res)}):")
for r in res:
    print(f"  - {r.get('id'):<38} score={r.get('match_score'):.2f}  "
          f"triggers={r.get('fired_triggers')}")

# Compute facts summary
facts = summarise_facts(briefing)
ml = facts.get("macro_levels") or {}
print(f"\nKey macro levels used:")
for k in ("kse100_close_in_range_pct", "kse100_intraday_range_pct",
          "kse100_open_to_close_pct", "brent_5d_slope_pct",
          "brent_usd_bbl", "kse100_ret_5d"):
    print(f"  {k}: {ml.get(k)}")
print(f"  days_to_next_event: {facts.get('cycle', {}).get('days_to_next_event') or briefing.get('days_to_next_event')}")
