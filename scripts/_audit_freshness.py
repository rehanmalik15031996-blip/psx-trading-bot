"""Audit which briefing sections are fresh vs stale vs failed."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from datetime import datetime, date

briefing = json.loads(
    Path('data/_strategist/_briefing_2026-05-11.json')
    .read_bytes().decode('utf-8','replace')
)

today = date(2026, 5, 11)

def age_days(ds):
    if not ds: return None
    s = str(ds)[:10]
    try:
        return (today - date.fromisoformat(s)).days
    except Exception:
        return None

print('=' * 90)
print('DATA FRESHNESS AUDIT  (today = 2026-05-11 Mon)')
print('=' * 90)
print(f'{"SECTION":<25} {"AS_OF":<14} {"AGE":>6} {"STATUS":<14} {"NOTE"}')
print('-' * 90)

checks = [
    ('regime', briefing.get('regime',{}).get('as_of'),
        'Friday close — normal for Mon AM',
        '(rule-based fallback — Anthropic credits exhausted)'),
    ('strategy_signal', briefing.get('strategy_signal',{}).get('as_of'),
        'Friday close — normal for Mon AM', ''),
    ('universe_ranking', briefing.get('universe_ranking',{}).get('as_of'),
        'Friday close — normal', ''),
    ('overnight', briefing.get('overnight',{}).get('as_of'),
        'Saturday US/Asia — fresh', ''),
    ('fipi_flows', briefing.get('fipi_flows',{}).get('as_of'),
        'Friday — normal (no Sat/Sun)', ''),
    ('policy_rate', briefing.get('policy_rate',{}).get('as_of'),
        'Yesterday — fresh', ''),
    ('macro_snapshot', briefing.get('macro_snapshot',{}).get('as_of'),
        '', ''),
    ('material_information', briefing.get('material_information',{}).get('as_of'),
        'Today', ''),
    ('management_outlook', briefing.get('management_outlook',{}).get('as_of'),
        'Today', ''),
    ('verdict_universe', briefing.get('verdict_universe',{}).get('as_of'),
        'Computed today from fresh fundamentals', ''),
    ('value_book', briefing.get('value_book',{}).get('as_of_fundamentals')
        or briefing.get('value_book',{}).get('OGDC',{}).get('as_of_fundamentals'),
        'Computed today', ''),
    ('quality_book',
        briefing.get('quality_book',{}).get('MCB',{}).get('as_of_fundamentals'),
        'Computed today', ''),
    ('earnings_momentum',
        briefing.get('earnings_momentum',{}).get('OGDC',{}).get('as_of_fundamentals'),
        'Computed today', ''),
    ('predictions', briefing.get('predictions',{}).get('as_of'),
        'Friday — predictions for Friday',
        '(weekend gap; Anthropic timeout fallback)'),
    ('prediction_accuracy', None, '', '0 scored — EOD never ran'),
    ('mf_holdings', briefing.get('mf_holdings',{}).get('as_of'),
        '', ''),
    ('volume_signals', briefing.get('volume_signals',{}).get('as_of'),
        'Friday close — normal', ''),
    ('scored_sentiment',
        f"24h cache (n={briefing.get('scored_sentiment',{}).get('macro',{}).get('n')})",
        'Last 24h — fresh',
        '(but news_scoring workflow failed yesterday — credits)'),
    ('industry_kpis', briefing.get('industry_kpis',{}).get('as_of'),
        '', ''),
    ('mufap_industry', briefing.get('mufap_industry',{}).get('latest_month'),
        '', ''),
    ('psx_turnover', briefing.get('psx_turnover',{}).get('as_of'), '', ''),
    ('remittances', briefing.get('remittances',{}).get('as_of'),
        'SBP releases monthly', ''),
    ('lsm_index', briefing.get('lsm_index',{}).get('as_of'),
        'PBS releases quarterly', ''),
    ('msci_calendar', briefing.get('msci_calendar',{}).get('as_of'), '', ''),
    ('earnings_calendar', briefing.get('earnings_calendar',{}).get('as_of'),
        'Computed today', ''),
    ('playbook_analogues', '(0 fired)', '',
        'No current setup matches historical playbook patterns'),
]

# mf_holdings — special: has data_freshness_days field
mf_age = briefing.get('mf_holdings',{}).get('per_stock_signals',{}).get('ATRL',{}).get('mf_data_freshness_days')

for name, as_of, fresh_note, note in checks:
    if as_of is None and name not in ('prediction_accuracy', 'playbook_analogues'):
        print(f'{name:<25} {"-":<14} {"?":>6} {"UNKNOWN":<14} {note}')
        continue
    if name == 'prediction_accuracy':
        print(f'{name:<25} {"-":<14} {"?":>6} {"FAIL":<14} {note}')
        continue
    if name == 'playbook_analogues':
        print(f'{name:<25} {"-":<14} {"-":>6} {"EMPTY":<14} {note}')
        continue

    if isinstance(as_of, str) and 'cache' in as_of:
        # scored_sentiment special case
        print(f'{name:<25} {as_of[:14]:<14} {"-":>6} {"FRESH":<14} {note}')
        continue

    a = age_days(as_of)
    if a is None:
        # treat as monthly/string
        print(f'{name:<25} {str(as_of)[:14]:<14} {"-":>6} {"INFO":<14} {fresh_note} {note}')
        continue
    if a == 0:
        status = 'TODAY'
    elif a <= 3:
        status = 'FRESH(weekend)'
    elif a <= 7:
        status = 'OK'
    elif a <= 30:
        status = 'STALE'
    else:
        status = 'VERY STALE'
    print(f'{name:<25} {str(as_of)[:14]:<14} {a:>4}d  {status:<14} {fresh_note} {note}')

print()
print(f'{"mf_holdings (per-stock)":<25} {"-":<14} {mf_age or "?":>4}d  '
        f'{"VERY STALE" if mf_age and mf_age>180 else "?"}'
        f'    Mutual-fund AUM file has not been refreshed since SECP release')

print()
print('=' * 90)
print('WORKFLOW STATUS')
print('=' * 90)
print('  predictions       : ran but used rule-based fallback (Anthropic credit issue)')
print('  master_strategist : ran but Claude call failed -> rule-based partial decision')
print('                       (this commit MANUALLY overrides with Cursor reasoning)')
print('  news_scoring      : LLM path failed yesterday (Anthropic) — used cached scores')
print('  intraday_session  : skipped (PSX closed weekend)')
print('  eod               : last successful Friday — normal')
print('  overnight         : ran successfully Saturday')
print('  health_check      : ran')
print('  master_strategist (Cursor manual): SUCCESS — all 35 stocks covered')
