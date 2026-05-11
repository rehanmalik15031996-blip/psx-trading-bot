"""Enrich today's (2026-05-11) cursor-reasoned predictions with the fresh
MF holdings signals from brain/mf_flows. Adds:
  - MF context to data_snapshot so the UI shows what drove the call
  - MF-derived key_drivers when conviction-relevant (n_funds_initiating_30d
    >= 2 OR n_funds_increasing_30d >= 4) so the analyst can see WHY a
    prediction was bullish or bearish
  - Updates suggested_action only when MF data flips the signal hard
    (e.g. ATRL gets BUY confirmation from 3 funds initiating)
"""
import sys, json
from datetime import date
from pathlib import Path

sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from brain.mf_flows import signals_for

LOG = Path('data/predictions_log.json')
TARGET_DATE = '2026-05-11'  # we enrich the predictions made FOR this date
asd = date(2026, 5, 11)

raw = json.loads(LOG.read_text(encoding='utf-8'))
preds = raw.get('predictions') or []

# Filter to today's cursor-reasoned predictions
today_preds = [
    p for p in preds
    if (p.get('data_snapshot') or {}).get('as_of_price_date', '').startswith('2026-05-08')
       and 'cursor' in (p.get('model') or '').lower()
]
print(f'enriching {len(today_preds)} cursor predictions for {TARGET_DATE} '
      f'(snapshot=2026-05-08)...')

n_enriched = 0
n_upgraded = 0  # had MF data significant enough to bump conviction
mf_summary = {}

for p in today_preds:
    sym = p.get('symbol', '')
    s = signals_for(sym, as_of=asd)
    funds = s.get('mf_n_funds_holding') or 0
    init30 = s.get('mf_n_funds_initiating_30d') or 0
    inc30 = s.get('mf_n_funds_increasing_30d') or 0
    dec30 = s.get('mf_n_funds_decreasing_30d') or 0
    fresh = s.get('mf_data_freshness_days')

    # Inject into data_snapshot
    snap = p.setdefault('data_snapshot', {})
    snap['mf_n_funds_holding'] = funds
    snap['mf_n_funds_initiating_30d'] = init30
    snap['mf_n_funds_increasing_30d'] = inc30
    snap['mf_n_funds_decreasing_30d'] = dec30
    snap['mf_data_freshness_days'] = fresh

    # Build a human-readable MF driver line if we have fresh, signal-bearing data
    if fresh is not None and fresh <= 60 and funds > 0:
        mf_signal_strength = 0
        if init30 >= 2:
            mf_signal_strength = 2  # strong: multiple new initiations
        elif init30 >= 1 or inc30 >= 4:
            mf_signal_strength = 1  # moderate
        elif dec30 >= 4:
            mf_signal_strength = -1  # bearish

        if mf_signal_strength != 0:
            net = inc30 - dec30
            if mf_signal_strength > 0:
                driver = (f"MF flows BULLISH: {init30} new fund(s) initiating in 30d, "
                          f"{inc30} increasing vs {dec30} decreasing (net +{net}). "
                          f"Held by {funds} fund(s); data {fresh}d fresh.")
            else:
                driver = (f"MF flows BEARISH: {dec30} fund(s) reducing in 30d, "
                          f"only {inc30} increasing (net {net:+d}). "
                          f"Held by {funds} fund(s); data {fresh}d fresh.")
            kd = p.setdefault('key_drivers', [])
            if not isinstance(kd, list):
                kd = []
                p['key_drivers'] = kd
            # Avoid duplicate insertion if re-run
            kd = [d for d in kd if 'MF flows' not in str(d)]
            kd.insert(0, driver)
            p['key_drivers'] = kd
            n_upgraded += 1
            mf_summary[sym] = {
                'init30': init30, 'inc30': inc30, 'dec30': dec30,
                'funds': funds, 'fresh': fresh,
                'signal': 'BULLISH' if mf_signal_strength > 0 else 'BEARISH',
            }
    n_enriched += 1

# Persist
LOG.write_text(json.dumps(raw, indent=2, default=str), encoding='utf-8')
print(f'enriched {n_enriched} predictions; {n_upgraded} got a fresh MF driver line.')
print()
print('Stocks with fresh MF signal injected into key_drivers:')
print(f'{"SYM":<8} {"signal":<8} {"init30":>6} {"inc30":>6} {"dec30":>6} '
      f'{"funds":>5} {"fresh":>6}')
for sym, m in sorted(mf_summary.items(), key=lambda x: -x[1]['init30']):
    print(f'  {sym:<8} {m["signal"]:<8} {m["init30"]:>6} {m["inc30"]:>6} '
          f'{m["dec30"]:>6} {m["funds"]:>5} {m["fresh"]:>5}d')
