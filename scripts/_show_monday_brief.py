"""Print Monday morning brief: Strategist decision + top predictions."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path

# ── Master Strategist ────────────────────────────────────────────────
from brain import master_strategist as ms
d = ms.load_cached()
print('=' * 65)
print('MASTER STRATEGIST  —  Monday May 11, 2026')
print('=' * 65)
stance = d.get('risk_stance', '?')
conv   = d.get('conviction', '?')
as_of  = str(d.get('as_of', '?'))[:10]
headline = d.get('headline', '')
print(f'Stance   : {stance}  ({conv})')
print(f'As of    : {as_of}')
print(f'Headline : {headline}')
print()

actions = d.get('actions') or []
if actions:
    print('Actions:')
    for a in actions[:10]:
        sym    = a.get('symbol') or '(market)'
        bucket = a.get('bucket', '?')
        aconv  = a.get('conviction', '?')
        reason = a.get('reason', '')[:72]
        print(f'  {bucket:6}  {sym:<8}  {aconv:<6}  {reason}')
else:
    print('Actions: none (full CASH mode)')

print()
macro_lens = d.get('macro_lens', '')
if macro_lens:
    print(f'Macro lens: {macro_lens[:200]}')
key_risks = d.get('key_risks') or []
if key_risks:
    print('Key risks:')
    for r in key_risks[:4]:
        print(f'  - {r}')

# ── Predictions ───────────────────────────────────────────────────────
print()
print('=' * 65)
print('LLM PREDICTIONS  —  Top signals')
print('=' * 65)

pred_path = Path('data/predictions_log.json')
pred_data = json.loads(pred_path.read_bytes().decode('utf-8', 'replace'))
preds = pred_data.get('predictions') or []

def _pred_date(p):
    return (p.get('generated_at') or '')[:10]

as_of_p = max(_pred_date(p) for p in preds) if preds else '?'
latest  = [p for p in preds if _pred_date(p) == as_of_p]
buys    = [p for p in latest if p.get('suggested_action') in ('BUY', 'ADD')]

print(f'As of: {as_of_p}  |  Total: {len(latest)}  |  BUY/ADD: {len(buys)}')
print()
print(f'{"Symbol":<8} {"Dir":<6} {"Action":<6} {"Conv":<5} {"Ret%":>6}  Rationale')
print('-' * 65)
sorted_preds = sorted(latest,
    key=lambda x: -(x.get('expected_return_5d_mid_pct') or 0))
for p in sorted_preds[:10]:
    sym    = p.get('symbol', '?')
    dirn   = (p.get('direction') or '?')[:5]
    action = p.get('suggested_action', '?')
    pconv  = (p.get('conviction') or '?')[:4]
    ret    = p.get('expected_return_5d_mid_pct') or 0
    rat    = str(p.get('rationale') or '')[:55]
    print(f'{sym:<8} {dirn:<6} {action:<6} {pconv:<5} {ret:+6.1f}%  {rat}')

print()
if buys:
    print('BUY/ADD candidates:')
    for p in buys:
        sym = p.get('symbol', '?')
        ret = p.get('expected_return_5d_mid_pct') or 0
        entry  = p.get('entry_price_pkr', '?')
        target = p.get('suggested_target_pkr', '?')
        stop   = p.get('suggested_stop_pkr', '?')
        print(f'  {sym}: entry={entry}  target={target}  stop={stop}  exp={ret:+.1f}%')
else:
    print('No BUY/ADD signals — market filter active (risk-off). '
          'Bot is in WATCH mode for Monday open.')
