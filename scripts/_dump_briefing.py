"""Dump the complete master strategist briefing to disk for inspection."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path

print('Calling brain.master_strategist.build_briefing()...')
print('(this calls every data source — may take ~30-60 seconds)')
print()

from brain import master_strategist as ms
briefing = ms.build_briefing()

out_path = Path('data/_strategist/_briefing_2026-05-11.json')
out_path.write_text(
    json.dumps(briefing, indent=2, default=str, ensure_ascii=False),
    encoding='utf-8'
)
print(f'Wrote briefing to: {out_path}')
print(f'Size: {out_path.stat().st_size / 1024:.1f} KB')
print()
print(f'Top-level keys ({len(briefing)}):')
for k in briefing.keys():
    v = briefing[k]
    if isinstance(v, (list, tuple)):
        meta = f'list[{len(v)}]'
    elif isinstance(v, dict):
        meta = f'dict[{len(v)} keys]'
    else:
        meta = str(type(v).__name__)
    print(f'  {k:<30} {meta}')
