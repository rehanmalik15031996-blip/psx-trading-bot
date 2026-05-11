"""Inspect refreshed briefing for new fields."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

b = json.loads(
    Path('data/_strategist/_briefing_2026-05-11.json')
    .read_bytes().decode('utf-8','replace'))

print('=== overnight (new global signals) ===')
ov = b.get('overnight', {})
print(f"as_of: {ov.get('as_of')}")
print(f"signals keys: {list(ov.get('signals',{}).keys())}")
sigs = ov.get('signals',{})
for k, v in sigs.items():
    if isinstance(v, dict):
        c = v.get('close')
        r1 = v.get('ret_1d_pct')
        print(f"  {k:<10} close={c}  1d={r1:+.2f}%" if r1 is not None else f"  {k:<10} close={c}")
print()
print(f'Briefing block (snippet):')
print(ov.get('briefing_block','')[:1500])
print()
print('=== playbook_analogues ===')
pa = b.get('playbook_analogues', [])
for c in pa:
    print(f"FIRED: {c.get('id')} - {c.get('title','')[:80]}")
    print(f"  triggers fired: {c.get('triggers_fired')}")
    print(f"  score: {c.get('score')}")
print()
print('=== prediction_accuracy ===')
pa2 = b.get('prediction_accuracy', {})
print(json.dumps(pa2, indent=2, default=str)[:600])
print()
print('=== predictions (today\'s) ===')
preds = b.get('predictions', {}).get('predictions', [])
print(f'Count: {len(preds)}')
print(f'as_of: {b.get("predictions",{}).get("as_of")}')
print('First 5:')
for p in preds[:5]:
    print(f"  {p['symbol']:<8} {p.get('direction','?'):<8} {p.get('suggested_action','?'):<6} mid={p.get('expected_return_5d_mid_pct',0):+.2f}%")
