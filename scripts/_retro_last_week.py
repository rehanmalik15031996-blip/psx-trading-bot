"""Retrospective: every prediction we made for the trading week May 4-9.

Reads data/predictions_log.json, filters to predictions whose data_snapshot
as_of_price_date is in [2026-05-04 .. 2026-05-08] (Mon-Fri), and reports:
  - what we said per stock per day
  - what actually happened (outcome.actual_return_pct)
  - hit / miss
  - aggregate accuracy
"""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from collections import defaultdict

log = json.loads(Path('data/predictions_log.json').read_bytes().decode('utf-8','replace'))
preds = log.get('predictions') or []

# Filter to last week
WEEK_DAYS = ['2026-05-04', '2026-05-05', '2026-05-06', '2026-05-07', '2026-05-08']
last_week = []
for p in preds:
    snap_date = (p.get('data_snapshot') or {}).get('as_of_price_date') or ''
    if snap_date in WEEK_DAYS:
        last_week.append(p)

print(f'Total predictions in log: {len(preds)}')
print(f'Predictions for week May 4-8: {len(last_week)}')
print()

# Group by date
by_date = defaultdict(list)
for p in last_week:
    snap_date = (p.get('data_snapshot') or {}).get('as_of_price_date') or '?'
    by_date[snap_date].append(p)

# Per-day report
n_total, n_hits_gross, n_hits_net, n_inside = 0, 0, 0, 0
sum_exp, sum_act, sum_act_net = 0.0, 0.0, 0.0
top_misses, top_wins = [], []

for date in sorted(by_date.keys()):
    rows = by_date[date]
    print(f'=== {date} ({len(rows)} predictions) ===')
    print(f'{"SYM":<8} {"DIR":<8} {"ACTION":<6} {"EXP%":>6} {"ACT%":>6} {"NET%":>6} {"HIT":<4} {"MODEL"}')
    for p in sorted(rows, key=lambda r: r.get('symbol','')):
        sym = p.get('symbol', '?')
        d = p.get('direction', '?')
        a = p.get('suggested_action', '?')
        exp = p.get('expected_return_5d_mid_pct', 0) or 0
        oc = p.get('outcome') or p.get('actual') or {}
        act = oc.get('actual_return_pct')
        act_net = oc.get('actual_return_net_pct')
        hit_gross = oc.get('direction_hit_gross') or oc.get('direction_hit')
        hit_net = oc.get('direction_hit_net')
        inside = oc.get('inside_range')
        model = (p.get('model') or '')[:18]

        if act is None:
            act_s, net_s, hit_s = '  -  ', '  -  ', ' - '
        else:
            act_s = f'{act:+.2f}'
            net_s = f'{act_net:+.2f}' if act_net is not None else '  -  '
            hit_s = 'OK' if hit_gross else 'X'
            n_total += 1
            if hit_gross: n_hits_gross += 1
            if hit_net: n_hits_net += 1
            if inside: n_inside += 1
            sum_exp += exp
            sum_act += act
            sum_act_net += act_net or 0
            err = abs(act - exp)
            if hit_gross:
                top_wins.append((date, sym, d, exp, act, err))
            else:
                top_misses.append((date, sym, d, exp, act, err))
        print(f'{sym:<8} {d:<8} {a:<6} {exp:+6.2f} {act_s:>6} {net_s:>6} {hit_s:<4} {model}')
    print()

# Aggregate
print('=' * 80)
print(f'WEEKLY AGGREGATE  (n={n_total} scored)')
print('=' * 80)
if n_total:
    print(f'Direction hit (gross): {n_hits_gross}/{n_total} = {100*n_hits_gross/n_total:.1f}%')
    print(f'Direction hit (net):   {n_hits_net}/{n_total} = {100*n_hits_net/n_total:.1f}%')
    print(f'Inside expected band:  {n_inside}/{n_total} = {100*n_inside/n_total:.1f}%')
    print(f'Avg expected mid:      {sum_exp/n_total:+.2f}%')
    print(f'Avg actual gross:      {sum_act/n_total:+.2f}%')
    print(f'Avg actual net:        {sum_act_net/n_total:+.2f}%')

print()
print('TOP 5 BIGGEST MISSES (worst expected-vs-actual error)')
print('-' * 80)
top_misses.sort(key=lambda r: r[5], reverse=True)
for date, sym, d, exp, act, err in top_misses[:8]:
    print(f'  {date}  {sym:<8} we said {d:<8} ({exp:+.2f}%)  '
          f'actual {act:+.2f}%  err={err:.2f}pp')

print()
print('TOP 5 WINS (largest correct moves)')
print('-' * 80)
top_wins.sort(key=lambda r: abs(r[4]), reverse=True)
for date, sym, d, exp, act, err in top_wins[:8]:
    print(f'  {date}  {sym:<8} we said {d:<8} ({exp:+.2f}%)  '
          f'actual {act:+.2f}%')

# Sector view
print()
print('PER-SECTOR HIT RATE')
print('-' * 60)
sector = defaultdict(lambda: [0,0])  # [hits, total]
for p in last_week:
    oc = p.get('outcome') or p.get('actual') or {}
    if oc.get('actual_return_pct') is None:
        continue
    s = p.get('sector', '?')
    sector[s][1] += 1
    if oc.get('direction_hit_gross') or oc.get('direction_hit'):
        sector[s][0] += 1

for s in sorted(sector.keys(), key=lambda x: -sector[x][1]):
    h, t = sector[s]
    pct = (100*h/t) if t else 0
    print(f'  {s:<24}  {h}/{t}  {pct:5.1f}%')
