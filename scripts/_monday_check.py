"""Monday readiness check — run before market open."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from datetime import date, timedelta

today = date.today()
print(f"Today: {today}  |  Monday readiness check\n")

results = []
health_dir = Path('data/_health')

# 1. EOD Prices
try:
    from ui.tools import get_price
    p = get_price('OGDC')
    as_of = p.get('as_of') or '?'
    close = p.get('close_pkr') or '?'
    age_days = (today - date.fromisoformat(as_of)).days if as_of != '?' else 99
    status = 'OK' if age_days <= 3 else 'WARN'
    results.append(('EOD Prices (OGDC)', status, f'close={close} as_of={as_of} ({age_days}d ago)'))
except Exception as e:
    results.append(('EOD Prices', 'FAIL', str(e)[:70]))

# 2. FIPI flows
try:
    from ui.tools import get_fipi_flows
    f = get_fipi_flows()
    regime = f.get('foreign_regime', '?')
    results.append(('FIPI flows', 'OK', f'regime={regime}'))
except Exception as e:
    results.append(('FIPI flows', 'FAIL', str(e)[:70]))

# 3. Macro snapshot
try:
    from ui.tools import get_macro_snapshot
    m = get_macro_snapshot()
    n_ind = len(m.get('indicators', {}))
    results.append(('Macro snapshot', 'OK', f'{n_ind} indicators'))
except Exception as e:
    results.append(('Macro snapshot', 'FAIL', str(e)[:70]))

# 4. Overnight globals
try:
    f_over = health_dir / 'overnight.json'
    if f_over.exists():
        ov = json.loads(f_over.read_bytes().decode('utf-8', 'replace'))
        ts = (ov.get('as_of') or ov.get('updated_at') or '')[:10]
        age = (today - date.fromisoformat(ts)).days if ts else 99
        status = 'OK' if age <= 3 else 'WARN'
        results.append(('Overnight globals', status, f'as_of={ts} ({age}d ago)'))
    else:
        results.append(('Overnight globals', 'FAIL', 'health file missing'))
except Exception as e:
    results.append(('Overnight globals', 'WARN', str(e)[:70]))

# 5. News sentiment
try:
    f_news = health_dir / 'news_scoring.json'
    if f_news.exists():
        ns = json.loads(f_news.read_bytes().decode('utf-8', 'replace'))
        ts = (ns.get('as_of') or ns.get('updated_at') or '')[:10]
        age = (today - date.fromisoformat(ts)).days if ts else 99
        status = 'OK' if age <= 3 else 'WARN'
        results.append(('News scoring', status, f'as_of={ts} ({age}d ago)'))
    else:
        results.append(('News scoring', 'WARN', 'health file missing'))
except Exception as e:
    results.append(('News scoring', 'WARN', str(e)[:70]))

# 6. Predictions
try:
    pred_path = Path('data/predictions_log.json')
    pred_data = json.loads(pred_path.read_bytes().decode('utf-8', 'replace'))
    preds = pred_data.get('predictions') or []
    if preds:
        def _pred_date(p):
            return (p.get('generated_at') or '')[:10] or \
                   (p.get('data_snapshot', {}).get('as_of_price_date') or '')
        as_of = max(_pred_date(p) for p in preds)
        latest = [p for p in preds if _pred_date(p) == as_of]
        age = (today - date.fromisoformat(as_of)).days if as_of else 99
        status = 'OK' if age <= 3 else 'WARN'
        results.append(('Predictions (LLM)', status,
                         f'{len(latest)} predictions as_of={as_of} ({age}d ago)'))
    else:
        results.append(('Predictions (LLM)', 'WARN', 'no predictions in log'))
except Exception as e:
    results.append(('Predictions (LLM)', 'FAIL', str(e)[:70]))

# 7. Master Strategist
try:
    from brain import master_strategist as ms
    d = ms.load_cached()
    if d:
        as_of = str(d.get('as_of', '?'))[:10]
        stance = d.get('risk_stance', '?')
        conv = d.get('conviction', '?')
        age = (today - date.fromisoformat(as_of)).days if as_of != '?' else 99
        status = 'OK' if age <= 3 else 'WARN'
        actions = d.get('actions') or []
        buys = [a.get('symbol') for a in actions if (a.get('bucket') or '').upper() in ('BUY','ADD') and a.get('symbol')]
        results.append(('Master Strategist', status,
                         f'stance={stance} ({conv}) as_of={as_of}  BUYs={buys}'))
    else:
        results.append(('Master Strategist', 'FAIL', 'no cached decision found'))
except Exception as e:
    results.append(('Master Strategist', 'FAIL', str(e)[:70]))

# 8. Strategy signal (Phase-1)
try:
    from ui.tools import get_strategy_signal
    s = get_strategy_signal()
    picks = s.get('selected_symbols') or s.get('selected') or []
    risk_on = s.get('market_risk_on', '?')
    as_of = s.get('as_of', '?')
    results.append(('Phase-1 signal', 'OK',
                     f'risk_on={risk_on}  picks={picks}  as_of={as_of}'))
except Exception as e:
    results.append(('Phase-1 signal', 'FAIL', str(e)[:70]))

# 9. Material info
try:
    f_mat = health_dir / 'material_info.json'
    if f_mat.exists():
        mat = json.loads(f_mat.read_bytes().decode('utf-8', 'replace'))
        ts = (mat.get('as_of') or mat.get('updated_at') or '')[:10]
        age = (today - date.fromisoformat(ts)).days if ts else 99
        status = 'OK' if age <= 4 else 'WARN'
        results.append(('Material info', status, f'as_of={ts} ({age}d ago)'))
    else:
        results.append(('Material info', 'WARN', 'health file missing'))
except Exception as e:
    results.append(('Material info', 'WARN', str(e)[:70]))

# 10. Financial results / Director reports
try:
    f_fin = health_dir / 'financial_results.json'
    if f_fin.exists():
        fin = json.loads(f_fin.read_bytes().decode('utf-8', 'replace'))
        ts = (fin.get('as_of') or fin.get('updated_at') or '')[:10]
        results.append(('Director reports', 'OK', f'as_of={ts}'))
    else:
        results.append(('Director reports', 'WARN', 'health file missing'))
except Exception as e:
    results.append(('Director reports', 'WARN', str(e)[:70]))

# Print report
print('DATA READINESS REPORT FOR MONDAY')
print('=' * 62)
ok = warn = fail = 0
for name, status, detail in results:
    icon = '[OK]  ' if status == 'OK' else '[WARN]' if status == 'WARN' else '[FAIL]'
    print(f'{icon} {name:<25} {detail}')
    if status == 'OK':   ok += 1
    elif status == 'WARN': warn += 1
    else:                  fail += 1

print()
print(f'Summary: {ok} OK  |  {warn} WARN  |  {fail} FAIL')
if fail == 0 and warn == 0:
    print('STATUS: FULLY READY FOR MONDAY')
elif fail == 0:
    print('STATUS: MOSTLY READY — review WARNs above')
else:
    print('STATUS: ACTION NEEDED — check FAILs above')
