"""Pull uncompressed per-stock data for full strategist reasoning."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path

briefing = json.loads(
    Path('data/_strategist/_briefing_2026-05-11.json').read_bytes().decode('utf-8','replace'))

ranking = briefing.get('universe_ranking', {}).get('ranking', [])
universe = [r['symbol'] for r in ranking]

print('Pulling uncompressed per-stock data...')

# Value book
from brain.valuation import universe_value_book
val_full = universe_value_book()
val = {r['symbol']: r for r in val_full.get('rows', []) if r.get('symbol')}
print(f'value_book: {len(val)} symbols')

# Quality book
from brain.quality import universe_quality_book, universe_earnings_momentum
qual_full = universe_quality_book()
qual = {r['symbol']: r for r in qual_full.get('rows', []) if r.get('symbol')}
print(f'quality_book: {len(qual)} symbols')

em_full = universe_earnings_momentum()
em_by_sym = {r['symbol']: r for r in em_full.get('rows', []) if r.get('symbol')}
print(f'earnings_momentum: {len(em_by_sym)} symbols')

# Verdict synthesizer
from brain.verdict_synthesizer import synthesize_universe
verdicts = synthesize_universe()
if isinstance(verdicts, dict):
    vu_by_sym = {}
    for k, v in verdicts.items():
        if isinstance(v, dict) and v.get('symbol'):
            vu_by_sym[v['symbol']] = v
    if not vu_by_sym and 'rows' in verdicts:
        vu_by_sym = {r['symbol']: r for r in verdicts['rows'] if r.get('symbol')}
elif isinstance(verdicts, list):
    vu_by_sym = {r['symbol']: r for r in verdicts if isinstance(r, dict) and 'symbol' in r}
else:
    vu_by_sym = {}
print(f'verdicts: {len(vu_by_sym)} symbols')

# Combine into per-stock dict
mf_per = briefing.get('mf_holdings', {}).get('per_stock_signals', {})
vol_per = briefing.get('volume_signals', {}).get('per_stock', {})
mi_by_sym = (briefing.get('macro_impact', {}).get('by_symbol') or {})
mi_by_sec = (briefing.get('macro_impact', {}).get('by_sector') or {})
preds = briefing.get('predictions', {}).get('predictions') or []
pred_by_sym = {p.get('symbol'): p for p in preds if p.get('symbol')}
mat_by_sym = {}
for r in briefing.get('material_information', {}).get('rows', []):
    s = r.get('symbol')
    if s:
        mat_by_sym.setdefault(s, []).append({'date': r.get('date'), 'title': r.get('title')[:80]})

mgmt = briefing.get('management_outlook', {})
mgmt_by_sym = {k: v for k, v in mgmt.items() if isinstance(v, dict) and v.get('symbol')}

# Earnings calendar
ec = briefing.get('earnings_calendar', {})
ec_by_sym = {r['symbol']: r for r in ec.get('upcoming', []) if r.get('symbol')}

# Build per-stock dict
per_stock = {}
for sym in universe:
    rrow = next((r for r in ranking if r['symbol'] == sym), {})
    val_row = val.get(sym, {}) if isinstance(val, dict) else {}
    qual_row = qual.get(sym, {}) if isinstance(qual, dict) else {}
    em_row = em_by_sym.get(sym, {})
    vu_row = vu_by_sym.get(sym, {})
    mf_row = mf_per.get(sym, {})
    vol_row = vol_per.get(sym, {})
    mi_row = mi_by_sym.get(sym, {}) or {}
    sector = rrow.get('sector', '?')
    mi_sec = mi_by_sec.get(sector, {}) or {}
    pred = pred_by_sym.get(sym, {})
    mat = mat_by_sym.get(sym, [])
    mg = mgmt_by_sym.get(sym, {})
    ec_row = ec_by_sym.get(sym, {})

    per_stock[sym] = {
        'symbol': sym,
        'sector': sector,
        'p1_score': rrow.get('mom_150d_log_ret', 0) or 0,
        'p1_rank': rrow.get('rank'),
        'rvol': rrow.get('rvol_20d_ann', 0),
        'close_pkr': rrow.get('close_pkr', 0),
        # Value
        'val_signal': val_row.get('signal', 'NO_SIGNAL'),
        'val_upside_pct': val_row.get('upside_pct'),
        'val_confidence': val_row.get('confidence'),
        'val_fair_value': val_row.get('fair_value'),
        # Quality
        'qual_score': qual_row.get('quality_score'),
        'qual_band': qual_row.get('band'),
        # Earnings momentum
        'em_flag': em_row.get('flag'),
        'em_yoy_pct': em_row.get('yoy_growth_pct'),
        # Verdict synthesizer (composite)
        'verdict_action': vu_row.get('action'),
        'verdict_direction': vu_row.get('direction'),
        'verdict_conviction': vu_row.get('conviction'),
        'verdict_score': vu_row.get('score'),
        # MF flows
        'mf_change_30d_pct': mf_row.get('mf_holding_change_30d_pct'),
        'mf_increasing_30d': mf_row.get('mf_n_funds_increasing_30d'),
        'mf_decreasing_30d': mf_row.get('mf_n_funds_decreasing_30d'),
        'mf_initiating_30d': mf_row.get('mf_n_funds_initiating_30d'),
        'mf_data_age_days': mf_row.get('mf_data_freshness_days'),
        # Volume
        'vol_breakout_3d': vol_row.get('had_breakout_3d', False),
        'vol_ratio_20d': vol_row.get('last_vol_ratio_20d'),
        # Macro impact
        'mi_score': mi_row.get('score', mi_sec.get('score', 0)),
        'mi_tailwinds': (mi_row.get('tailwinds') or mi_sec.get('tailwinds') or [])[:2],
        'mi_headwinds': (mi_row.get('headwinds') or mi_sec.get('headwinds') or [])[:2],
        # Predictions
        'pred_dir': pred.get('direction'),
        'pred_action': pred.get('suggested_action'),
        'pred_conv': pred.get('conviction'),
        'pred_ret': pred.get('expected_return_5d_mid_pct'),
        # Material info
        'material_info': mat[:2],
        # Management outlook
        'mgmt_tone': mg.get('outlook_tone') if mg else None,
        'mgmt_summary': (mg.get('outlook_summary', '') or '')[:120] if mg else None,
        # Earnings calendar
        'next_earnings_days': ec_row.get('days_until') if ec_row else None,
        'in_blackout': ec_row.get('in_blackout_5d', False) if ec_row else False,
    }

out = Path('data/_strategist/_per_stock_2026-05-11.json')
out.write_text(json.dumps(per_stock, indent=2, default=str, ensure_ascii=False), encoding='utf-8')
print()
print(f'Wrote per-stock data to: {out}')
print(f'Size: {out.stat().st_size / 1024:.1f} KB')

# Print compact view
print()
print('=' * 130)
print(f'{"SYM":<8} {"SECTOR":<22} {"P1":>5} {"VAL":<11} {"UPS%":>6} {"QUAL":<6} {"EM":<13} {"VRDC":<5} {"MF30%":>6} {"VOL_BO":<7} {"MI":>5} {"PRED":<10}')
print('=' * 130)
for sym, d in per_stock.items():
    val_s = (d['val_signal'] or '?')[:11]
    ups = d['val_upside_pct']
    ups_s = f'{ups:+.0f}%' if isinstance(ups, (int, float)) else '-'
    qb = (d['qual_band'] or '?')[:6]
    em = (d['em_flag'] or '?')[:13]
    vrdc = (d['verdict_action'] or '?')[:5]
    mf = d['mf_change_30d_pct']
    mf_s = f'{mf:+.1f}' if isinstance(mf, (int, float)) else '-'
    bo = 'YES' if d['vol_breakout_3d'] else '-'
    mi = d['mi_score'] or 0
    pred_d = (d['pred_dir'] or '?')[:4]
    pred_a = (d['pred_action'] or '?')[:4]
    pred_s = f'{pred_d}/{pred_a}'[:10]
    print(f'{sym:<8} {d["sector"][:21]:<22} {d["p1_score"]:+.2f} {val_s:<11} {ups_s:>6} {qb:<6} {em:<13} {vrdc:<5} {mf_s:>6} {bo:<7} {mi:+.2f} {pred_s:<10}')
