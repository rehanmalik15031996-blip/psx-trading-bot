"""Pull each day's strategist headline + actions for May 11-14 (the
working week that ended Thursday). Print as a single table for the
post-mortem."""
import json
from pathlib import Path

import pandas as pd

dates = ["2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14"]

print("=" * 84)
print("  Strategist daily calls, May 11-14")
print("=" * 84)

for d in dates:
    p = Path("data/_strategist") / f"{d}.json"
    if not p.exists():
        print(f"\n--- {d} : MISSING ---")
        continue
    body = json.loads(p.read_text(encoding="utf-8"))
    print(f"\n--- {d} ({body.get('model', 'unknown')[:30]}) ---")
    print(f"  Headline:   {body.get('headline', '')[:120]}")
    print(f"  Stance:     {body.get('risk_stance')}  "
          f"(conviction: {body.get('conviction')})")
    print(f"  Fallback:   {body.get('fallback_used')}")
    narrative = (body.get('narrative') or '')[:300]
    if narrative:
        print(f"  Narrative:  {narrative}")
    actions = body.get('actions') or []
    if actions:
        print(f"  Actions ({len(actions)}):")
        for a in actions[:8]:
            sym = a.get('symbol') or '-'
            bucket = a.get('bucket', '?')
            conv = a.get('conviction', '?')
            tw = a.get('target_weight_pct')
            tw_s = f"{tw:.1f}%" if isinstance(tw, (int, float)) else "—"
            reason = (a.get('reason') or '')[:70]
            print(f"    {sym:<8} {bucket:<6} {conv:<8} weight={tw_s:<7} {reason}")

# v2 cache
print("\n" + "=" * 84)
print("  v2 cache (Thursday, after the new pipeline was deployed)")
print("=" * 84)
p = Path("data/_strategist/latest_v2.json")
v2 = json.loads(p.read_text(encoding="utf-8"))
print(f"\nHeadline:  {v2.get('headline')}")
print(f"Regime:    {v2.get('regime')}")
print(f"\nTop long ideas with position plans:")
for idea in v2['long_ideas']['ideas'][:5]:
    pp = idea.get('position_plan') or {}
    print(f"  {idea['symbol']:<7} [{idea['action']:<4} {idea['conviction']:<7}] "
          f"score {idea['score']:+.2f}  "
          f"entry={pp.get('entry_price')}  "
          f"stop={pp.get('stop_loss_pct')}%  "
          f"target={pp.get('target_pct')}%  "
          f"size={pp.get('position_size_pct')}%")
    print(f"     why: {idea.get('why', '')[:120]}")
