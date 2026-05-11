"""Extract per-stock data from the briefing for strategist reasoning."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

briefing = json.loads(
    Path('data/_strategist/_briefing_2026-05-11.json').read_bytes().decode('utf-8','replace'))

# Universe symbols from ranking
ranking = briefing.get('universe_ranking', {}).get('ranking', [])
universe = [r['symbol'] for r in ranking]
print(f'Universe: {len(universe)} symbols')

# Phase-1 picks
sig = briefing.get('strategy_signal', {})
print(f'Phase-1 risk_on: {sig.get("market_risk_on")}')
print(f'Phase-1 selected: {sig.get("selected_symbols")}')
print(f'Phase-1 would-pick: {sig.get("would_pick_if_market_filter_off")}')
print()

# Per-stock indexes
val_book = briefing.get('value_book', {}).get('rows') or []
val_by_sym = {r.get('symbol'): r for r in val_book if r.get('symbol')}

qual_book = briefing.get('quality_book', {}).get('rows') or []
qual_by_sym = {r.get('symbol'): r for r in qual_book if r.get('symbol')}

vu = briefing.get('verdict_universe', {}).get('rows') or []
vu_by_sym = {r.get('symbol'): r for r in vu if r.get('symbol')}

mf = briefing.get('mf_holdings', {})
mf_signals = mf.get('per_symbol') or mf.get('signals') or {}

vol = briefing.get('volume_signals', {})
vol_signals = vol.get('per_symbol') or vol.get('signals') or {}

mi = briefing.get('macro_impact', {})
mi_by_sym = (mi.get('by_symbol') or {})
mi_by_sec = (mi.get('by_sector') or {})

preds = briefing.get('predictions', {}).get('predictions') or []
pred_by_sym = {p.get('symbol'): p for p in preds if p.get('symbol')}

# Print combined per-stock view
print('='*120)
print(f'{"SYM":<8} {"SECTOR":<22} {"P1":>4} {"VAL":>5} {"QUAL":>5} {"MF":>4} {"VOL":>5} {"VRDC":>6} {"PRED":>6} {"MI":>5}')
print('='*120)

for sym in universe:
    rrow = next((r for r in ranking if r['symbol']==sym), {})
    sector = (rrow.get('sector') or '?')[:21]
    p1_score = rrow.get('mom_150d_log_ret', 0) or 0

    vrow = val_by_sym.get(sym, {})
    val_str = vrow.get('signal','?')[:5] if vrow else '-'

    qrow = qual_by_sym.get(sym, {})
    qual_str = qrow.get('grade', '?')[:5] if qrow else '-'

    mf_str = '?'
    if isinstance(mf_signals, dict) and sym in mf_signals:
        mf_sig = mf_signals[sym]
        if isinstance(mf_sig, dict):
            mf_str = mf_sig.get('signal','?')[:4]

    vol_str = '?'
    if isinstance(vol_signals, dict) and sym in vol_signals:
        vs = vol_signals[sym]
        if isinstance(vs, dict):
            vol_str = vs.get('signal','?')[:5]

    vrdc = vu_by_sym.get(sym, {})
    vrdc_str = vrdc.get('verdict','?')[:6] if vrdc else '-'

    pred = pred_by_sym.get(sym, {})
    pred_dir = pred.get('direction','?')[:4] if pred else '-'
    pred_act = pred.get('suggested_action','?')[:4] if pred else '-'
    pred_str = f'{pred_dir}/{pred_act}'[:6]

    misig = mi_by_sym.get(sym, {})
    mi_score = misig.get('score', 0) or 0 if isinstance(misig, dict) else 0

    print(f'{sym:<8} {sector:<22} {p1_score:+.2f} {val_str:>5} {qual_str:>5} {mf_str:>4} {vol_str:>5} {vrdc_str:>6} {pred_str:>6} {mi_score:+.2f}')

# Also print structures of unfamiliar dicts
print()
print('=== verdict_universe sample ===')
if vu:
    print(json.dumps(vu[0], indent=2, default=str)[:800])
print()
print('=== value_book sample ===')
if val_book:
    print(json.dumps(val_book[0], indent=2, default=str)[:600])
print()
print('=== quality_book sample ===')
if qual_book:
    print(json.dumps(qual_book[0], indent=2, default=str)[:600])
print()
print('=== mf_holdings keys ===')
print(list(mf.keys()))
if mf_signals:
    sample_key = list(mf_signals.keys())[0] if isinstance(mf_signals, dict) else None
    if sample_key:
        print(f'Sample mf_signal for {sample_key}:')
        print(json.dumps(mf_signals[sample_key], indent=2, default=str)[:400])
print()
print('=== volume_signals keys ===')
print(list(vol.keys()))
if vol_signals:
    sample_key = list(vol_signals.keys())[0] if isinstance(vol_signals, dict) else None
    if sample_key:
        print(f'Sample vol_signal for {sample_key}:')
        print(json.dumps(vol_signals[sample_key], indent=2, default=str)[:400])
print()
print('=== top_buys ===')
top_buys = briefing.get('top_buys', {})
print(json.dumps({k:v for k,v in top_buys.items() if k != 'raw'}, indent=2, default=str)[:1500])
print()
print('=== short_candidates ===')
sc = briefing.get('short_candidates', {})
print(json.dumps({k:v for k,v in sc.items() if k != 'raw'}, indent=2, default=str)[:1500])
print()
print('=== material_information keys ===')
mat = briefing.get('material_information',{})
print(list(mat.keys()))
print(json.dumps(mat, indent=2, default=str)[:1200])
print()
print('=== management_outlook keys ===')
mgmt = briefing.get('management_outlook',{})
print(list(mgmt.keys()))
print(json.dumps({k:v for k,v in mgmt.items() if k != 'raw'}, indent=2, default=str)[:1200])
print()
print('=== playbook_analogues ===')
print(briefing.get('playbook_analogues'))
print()
print('=== psx_turnover, remittances, lsm_index ===')
for k in ['psx_turnover','remittances','lsm_index','mufap_industry']:
    print(f'\n{k}:')
    print(json.dumps(briefing.get(k,{}), indent=2, default=str)[:600])
