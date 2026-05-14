"""Measure size of saved briefings to estimate the LLM payload."""
import json
from pathlib import Path

candidates = sorted(Path("data/_strategist").glob("_briefing_*.json"))
if not candidates:
    print("no saved briefings"); raise SystemExit
p = candidates[-1]
print(f"Using {p.name}")
briefing = json.loads(p.read_text(encoding="utf-8"))
raw = json.dumps(briefing, default=str, indent=2)
print(f"Briefing JSON: {len(raw):,} chars (~{len(raw)//4:,} tokens)")
print(f"Cap in prompt:   120,000 chars (~30,000 tokens)")
print(f"  -> {'TRUNCATED' if len(raw) > 120_000 else 'fits within cap'}")
print()
items = []
for k, v in briefing.items():
    s = len(json.dumps(v, default=str))
    items.append((k, s))
items.sort(key=lambda kv: -kv[1])
total = sum(s for _, s in items)
print(f"{'top-level key':<38} {'bytes':>10} {'tokens':>9} {'share%':>8}")
print("-" * 75)
for k, s in items[:25]:
    print(f"  {k:<36} {s:>10,} {s//4:>9,} {s/total*100:>6.1f}%")
remainder = sum(s for _, s in items[25:])
if remainder:
    print(f"  ...{len(items)-25} more:                          "
          f"{remainder:>10,}")
print(f"\nTOTAL: {total:,} bytes (~{total//4:,} tokens)")
print()
print(f"Universe: {len(briefing.get('universe', []) or [])} stocks")
ana = briefing.get("playbook_analogues") or []
print(f"Playbook analogues: {len(ana)}")
ranked = briefing.get("ranked_top_today") or briefing.get("candidates") or []
print(f"Ranked candidates: {len(ranked)}")
