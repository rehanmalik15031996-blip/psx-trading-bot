"""Fix the playbook_analogue_fired schema in strategist JSONs so the UI
renders correctly. The UI's _render_playbook_analogues expects each value
to be a dict with keys: fired_triggers, match_score, confidence (the
shape produced by brain/master_strategist._briefing_summary). Hand-written
JSONs from May-11 stored a free-text string instead, which triggered an
'str object has no attribute get' crash on Streamlit Cloud.

Also re-injects fresh AMC FMR data:
  - mf_data_freshness_days dropped from 344 to ~40
  - n_funds_increasing universe metric is now populated (~45)
  - Per-stock signals (mf_n_funds_initiating_30d) are now actionable
"""
import sys, json, copy
from datetime import date
from pathlib import Path

sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from brain.mf_flows import (
    data_freshness_days, n_funds_increasing_universe, signals_for,
)

ROOT = Path('.')
TARGETS = [
    ROOT / 'data' / '_strategist' / '2026-05-11.json',
    ROOT / 'data' / '_strategist' / 'latest.json',
]

# Canonical fired-triggers payload. Built to match what
# brain/master_strategist._briefing_summary writes.
ANALOGUE_FIRED = {
    "imf_review_mission_week": {
        "fired_triggers": ["event:imf_mission_active"],
        "match_score": 0.85,
        "confidence": "MEDIUM",
        "strategist_note": (
            "IMF review mission active in Islamabad (arrives May 15). "
            "Defensive posture, large-cap bias, binary risk. Reduce "
            "gross exposure to 30-40% of normal in the week BEFORE the "
            "mission. Hold quality (MCB, UBL, FFC, OGDC). Avoid "
            "leveraged names."
        ),
    }
}

# Refreshed MF lens that now has REAL fresh signal (was stale before AMC
# FMR scraper landed).
asd = date(2026, 5, 11)
mf_universe = {
    "data_freshness_days": data_freshness_days(asd),
    "n_funds_increasing_universe": n_funds_increasing_universe(asd),
}

# Per-stock highlights for the action set
mf_per_stock = {}
for sym in ("OGDC", "PPL", "MARI", "ATRL", "PSO", "HUBC", "MEBL",
            "FFC", "EFERT", "BAHL", "MCB", "UBL", "HBL", "LUCK",
            "DGKC", "MLCF", "FCCL", "SYS"):
    s = signals_for(sym, as_of=asd)
    mf_per_stock[sym] = {
        "n_funds_holding": s.get("mf_n_funds_holding"),
        "n_funds_initiating_30d": s.get("mf_n_funds_initiating_30d"),
        "n_funds_increasing_30d": s.get("mf_n_funds_increasing_30d"),
        "n_funds_decreasing_30d": s.get("mf_n_funds_decreasing_30d"),
        "data_freshness_days": s.get("mf_data_freshness_days"),
    }

for path in TARGETS:
    if not path.exists():
        print(f"skip {path} (not found)")
        continue
    obj = json.loads(path.read_text(encoding='utf-8'))
    bs = obj.get('briefing_summary') or {}
    # Fix the shape
    bs['playbook_analogue_fired'] = ANALOGUE_FIRED
    # Refresh the MF universe summary with fresh AMC FMR data
    bs['mf_universe'] = mf_universe
    bs['mf_per_stock_highlights'] = mf_per_stock
    obj['briefing_summary'] = bs
    path.write_text(json.dumps(obj, indent=2, default=str), encoding='utf-8')
    print(f'patched {path}')

print()
print(f'mf_universe injected: {mf_universe}')
print('mf_per_stock_highlights (selection):')
for sym in ('OGDC', 'ATRL', 'PSO', 'BAHL', 'HUBC'):
    s = mf_per_stock[sym]
    print(f'  {sym:<8} funds={s["n_funds_holding"]} '
          f'init30={s["n_funds_initiating_30d"]} '
          f'inc30={s["n_funds_increasing_30d"]} '
          f'fresh={s["data_freshness_days"]}d')
