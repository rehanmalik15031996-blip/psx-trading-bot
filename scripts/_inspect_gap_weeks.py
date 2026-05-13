"""Inspect why specific dates produced no fires."""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import playbook as pb
from scripts.replay_briefing import replay_briefing, HISTORICAL_EVENTS
from scripts._research_backtest import _replay_active_events

DATES = [
    "2022-02-18",  # Russia-Ukraine pre-invasion
    "2025-05-02",  # Universe -6.71% 5d
    "2024-12-20",  # Missed +5.51% rally
    "2024-12-27",  # Missed +3.64% rally
]

import scripts._research_backtest as rb

for date_str in DATES:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    rb._REPLAY_AS_OF = d
    pb._load_active_events = rb._replay_active_events  # noqa: SLF001
    b = replay_briefing(d)
    print(f"\n{'=' * 70}")
    print(f"AS_OF: {d}")
    print(f"{'=' * 70}")
    facts = pb.summarise_facts(b)
    print(f"  regime:           {facts.get('regime')}")
    print(f"  universe_5d:      {facts.get('universe_5d')}")
    print(f"  universe_21d:     {facts.get('universe_21d')}")
    print(f"  breadth:          {facts.get('breadth')}")
    print(f"  active_events:    {sorted(facts.get('active_events') or set())}")
    print(f"  drivers:          {facts.get('drivers')}")
    print(f"  policy_rate:      {facts.get('cycle', {}).get('policy_rate_pct')}")
    print(f"  days_since_last_cut: {facts.get('cycle', {}).get('days_since_last_cut')}")
    print(f"  days_since_last_hike: {facts.get('cycle', {}).get('days_since_last_hike')}")
    macro = facts.get('macro_levels') or {}
    print(f"  brent_last:       {macro.get('brent_last')}")
    print(f"  usdpkr_last:      {macro.get('usdpkr_last')}")

    analogues = pb.retrieve_analogues(b, top_k=10) or []
    if not analogues:
        # Show top scores even when nothing fired
        cases = pb.load_cases()
        scored = []
        facts2 = pb.summarise_facts(b)
        for case in cases:
            res = pb._score_case(case, facts2)
            # _score_case may return tuple of varying arity in this codebase
            if isinstance(res, tuple) and len(res) >= 2:
                score = res[0]
                fired = res[1]
            else:
                score = float(res or 0)
                fired = []
            scored.append((getattr(case, "id", None) or getattr(case, "case_id", "?"), score, fired))
        scored.sort(key=lambda x: -(x[1] or 0))
        print(f"  fired cases:      [] (none)")
        print(f"  TOP 5 case scores (no fire):")
        for cid, s, f in scored[:5]:
            print(f"    {cid}: score={s:.2f} fired={f}")
    else:
        print(f"  fired cases ({len(analogues)}):")
        for a in analogues[:8]:
            print(f"    {a['id']}: score={a['match_score']:.2f}  triggers={a['fired_triggers']}")
