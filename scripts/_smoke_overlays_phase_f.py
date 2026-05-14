"""Smoke test: the new Phase F cases apply overlays correctly."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain.playbook import retrieve_analogues
from brain.strategist_overlays import apply_playbook_overlays, compute_predictor_bias

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
                                "kse100_ret_21d": -0.030}},
    "macro_snapshot": {"indicators": {
        "brent": {"last": 105.0},
        "usdpkr": {"last": 282.4},
    }},
    "regime": {"current_regime": "NORMAL"},
    "days_to_next_event": 1,
}

# Synthetic strategist decision before overlays
decision = {
    "actions": [
        {"symbol": "HBL",  "sector": "Banking", "bucket": "BUY",  "conviction": "MEDIUM"},
        {"symbol": "MCB",  "sector": "Banking", "bucket": "HOLD", "conviction": "MEDIUM"},
        {"symbol": "DGKC", "sector": "Cement",  "bucket": "BUY",  "conviction": "MEDIUM"},
        {"symbol": "MLCF", "sector": "Cement",  "bucket": "HOLD", "conviction": "LOW"},
        {"symbol": "HUBC", "sector": "Power",   "bucket": "BUY",  "conviction": "HIGH"},
        {"symbol": "OGDC", "sector": "Oil & Gas E&P", "bucket": "BUY", "conviction": "HIGH"},
        {"symbol": "POL",  "sector": "Oil & Gas E&P", "bucket": "BUY", "conviction": "MEDIUM"},
        {"symbol": "PSO",  "sector": "OMC",     "bucket": "WATCH", "conviction": "LOW"},
    ],
    "cash_pct": 20,
    "conviction": "HIGH",
    "narrative": "Pre-overlay base decision",
}

# Inject fired playbook into briefing (key the overlay expects)
fired = retrieve_analogues(briefing_may14, top_k=10, min_score=1.0)
briefing_may14["playbook_analogues"] = fired
print(f"Fired cases: {[f.get('id') for f in fired]}")

# Apply overlays
out = apply_playbook_overlays(decision, briefing_may14)
print(f"\n--- Decision after overlays ---")
print(f"Cash floor:    {out.get('cash_pct')}%")
print(f"Conviction:    {out.get('conviction')}")
print(f"\nPer-symbol changes:")
for a in out.get("actions", []):
    if a.get("symbol") in {"HBL", "MCB", "DGKC", "MLCF", "HUBC", "OGDC", "POL", "PSO"}:
        print(f"  {a['symbol']:<6} {a.get('sector'):<18} bucket={a.get('bucket'):<6} "
              f"conv={a.get('conviction')}")

print(f"\nOverlay log:")
for e in out.get("playbook_overlay_log", []):
    cid = e.get("case_id")
    chs = e.get("changes", [])
    if chs:
        print(f"  [{cid}]")
        for ch in chs[:5]:
            print(f"     {ch}")

print(f"\n--- Predictor bias from same fired cases ---")
bias = compute_predictor_bias(briefing_may14)
print(f"Sector bias:")
for s, v in (bias.get("sector_bias") or {}).items():
    print(f"  {s:<22} {v:+.3f}")
print(f"Symbol bias:")
for s, v in (bias.get("symbol_bias") or {}).items():
    print(f"  {s:<22} {v:+.3f}")
