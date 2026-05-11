"""Find any case in cases.json with a reaction value that is NOT a dict.
That would crash ui/app.py:_render_playbook_analogues at r.get('d21')."""
import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

raw = json.loads(Path('data/playbook/cases.json').read_text(encoding='utf-8'))
cases = raw.get('cases') if isinstance(raw, dict) else raw
if cases is None:
    cases = raw

bad: list[str] = []
n_total = 0
for c in cases:
    cid = c.get('id', '?')
    for inst in (c.get('historical_instances') or []):
        idate = inst.get('date', '?')
        rxns = inst.get('reactions') or {}
        if not isinstance(rxns, dict):
            bad.append(f"{cid}/{idate}: reactions itself is {type(rxns).__name__}")
            continue
        for sym, r in rxns.items():
            n_total += 1
            if not isinstance(r, dict):
                bad.append(f"{cid}/{idate}/{sym}: reaction is {type(r).__name__} = {r!r}")

print(f"Scanned {n_total} reactions across {len(cases)} cases.")
if bad:
    print(f"\n{len(bad)} BAD reactions:")
    for b in bad:
        print(f"  {b}")
else:
    print("All reactions are dicts.")

print()
print("Now scanning the strategist's playbook_analogue_fired structure...")
for path in ['data/_strategist/2026-05-11.json', 'data/_strategist/latest.json']:
    p = Path(path)
    if not p.exists():
        continue
    d = json.loads(p.read_text(encoding='utf-8'))
    fired = d.get('playbook_analogue_fired')
    print(f'\n{path}:')
    print(f'  type(fired) = {type(fired).__name__}')
    if isinstance(fired, dict):
        for cid, meta in fired.items():
            print(f'  {cid}: type={type(meta).__name__}', end='')
            if isinstance(meta, dict):
                trig = meta.get('fired_triggers')
                print(f'  fired_triggers type={type(trig).__name__}')
            else:
                print(f' value={meta!r}')
