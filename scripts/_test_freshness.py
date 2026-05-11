"""Verify mf_data_freshness_days dropped after AMC FMR integration."""
import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from datetime import date
from brain.mf_flows import signals_for, _load_holdings

# Force re-load (clear cache)
from brain import mf_flows
mf_flows._CACHE = mf_flows._Cache()

df = _load_holdings()
print(f'union holdings parquet: {len(df) if df is not None else 0:,} rows')
if df is not None and not df.empty:
    print(f'  months  : {sorted(df["as_of_month"].dt.date.unique())}')
    print(f'  funds   : {df["fund_name"].nunique()}')
    print(f'  symbols : {df["symbol"].nunique()}')

print()
print(f'{"SYM":<8} {"funds":>6} {"fresh_d":>8} {"chg_30d":>8} {"chg_90d":>8} '
      f'{"init_30d":>9} {"streak_acc":>10}')
for sym in ['OGDC', 'PPL', 'MARI', 'POL', 'FFC', 'EFERT', 'HUBC', 'KAPCO',
            'ATRL', 'PSO', 'APL', 'MEBL', 'MCB', 'HBL', 'UBL', 'BAHL',
            'LUCK', 'DGKC', 'MLCF', 'FCCL', 'KOHC', 'SYS', 'TRG']:
    s = signals_for(sym, as_of=date(2026, 5, 11))
    funds = s.get('mf_n_funds_holding') or 0
    fresh = s.get('mf_data_freshness_days')
    chg30 = s.get('mf_holding_change_30d_pct')
    chg90 = s.get('mf_holding_change_90d_pct')
    init30 = s.get('mf_n_funds_initiating_30d') or 0
    streak = s.get('mf_accumulation_streak') or 0
    print(f'{sym:<8} {funds:>6d} {fresh!s:>8} '
          f'{chg30 if chg30 is not None else "-":>8} '
          f'{chg90 if chg90 is not None else "-":>8} '
          f'{init30:>9d} {streak:>10}')
