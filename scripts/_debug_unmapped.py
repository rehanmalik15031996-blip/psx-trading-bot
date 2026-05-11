"""Re-parse Al Meezan PDF and dump unmapped company names."""
import sys
from pathlib import Path
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from scripts.ingest_amc_fmr import parse_fmr_pdf, map_company_to_symbol

pdf = Path('data/raw/amc_fmr/almeezan/2026-04.pdf')
rows = parse_fmr_pdf(pdf)
print(f'Total parsed rows: {len(rows)}')
print(f'Funds detected: {sorted({r["fund_name"] for r in rows})}')
print()

# Show all unique unmapped names with their funds
unmapped: dict[str, list[str]] = {}
for r in rows:
    if r['symbol'] == '':
        unmapped.setdefault(r['stock_name_raw'], []).append(r['fund_name'])

print(f'UNMAPPED NAMES ({len(unmapped)} unique):')
for name in sorted(unmapped.keys()):
    funds = sorted(set(unmapped[name]))
    print(f'  "{name}"  -> seen in {len(funds)} fund(s): {funds[:2]}')

print()
print(f'MAPPED NAMES (sample):')
mapped: dict[str, str] = {}
for r in rows:
    if r['symbol']:
        mapped[r['stock_name_raw']] = r['symbol']
for name, sym in sorted(mapped.items()):
    print(f'  "{name}" -> {sym}')
