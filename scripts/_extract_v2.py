"""Extract per-stock data from briefing - v2 with correct keys."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

briefing = json.loads(
    Path('data/_strategist/_briefing_2026-05-11.json').read_bytes().decode('utf-8','replace'))

# Universe
ranking = briefing.get('universe_ranking', {}).get('ranking', [])
universe = [r['symbol'] for r in ranking]

# Inspect actual structures
print('=== value_book keys ===')
print(list(briefing.get('value_book',{}).keys()))
vb = briefing.get('value_book',{})
print('Sample value_book:')
print(json.dumps(vb, indent=2, default=str)[:2000])

print()
print('=== quality_book keys ===')
qb = briefing.get('quality_book',{})
print(list(qb.keys()))
print('Sample quality_book:')
print(json.dumps(qb, indent=2, default=str)[:1500])

print()
print('=== verdict_universe keys ===')
vu = briefing.get('verdict_universe',{})
print(list(vu.keys()))
print('Sample verdict_universe:')
print(json.dumps(vu, indent=2, default=str)[:2500])

print()
print('=== mf_holdings.per_stock_signals sample ===')
mf = briefing.get('mf_holdings',{})
pss = mf.get('per_stock_signals',{})
print(f'Keys: {list(pss.keys())[:5]}')
if pss:
    sample = list(pss.keys())[0]
    print(f'Sample for {sample}:')
    print(json.dumps(pss[sample], indent=2, default=str)[:600])

print()
print('=== volume_signals.per_stock sample ===')
vs = briefing.get('volume_signals',{})
ps = vs.get('per_stock',{})
print(f'Keys: {list(ps.keys())[:5]}')
if ps:
    sample = list(ps.keys())[0]
    print(f'Sample for {sample}:')
    print(json.dumps(ps[sample], indent=2, default=str)[:600])

print()
print('=== earnings_momentum keys ===')
em = briefing.get('earnings_momentum',{})
print(list(em.keys()))
print(json.dumps(em, indent=2, default=str)[:1500])

print()
print('=== earnings_calendar ===')
ec = briefing.get('earnings_calendar',{})
print(json.dumps(ec, indent=2, default=str)[:1500])

print()
print('=== top_buys.ideas (all) ===')
tb = briefing.get('top_buys',{})
ideas = tb.get('ideas',[])
print(f'Total ideas: {len(ideas)}')
for i, idea in enumerate(ideas[:5]):
    sym = idea.get('symbol')
    sector = idea.get('sector')
    rationale = idea.get('rationale',{})
    verdict = rationale.get('verdict','?') if isinstance(rationale, dict) else '?'
    headline = rationale.get('headline','')[:80] if isinstance(rationale,dict) else ''
    print(f'  {sym:<8} {sector:<22} {verdict:<6} {headline}')

print()
print('=== management_outlook (all) ===')
mgmt = briefing.get('management_outlook',{})
for k, v in mgmt.items():
    if isinstance(v, dict) and 'symbol' in v:
        sym = v['symbol']
        tone = v.get('outlook_tone','?')
        plans = len(v.get('growth_plans',[]))
        risks = len(v.get('risks_mentioned',[]))
        outlook = (v.get('outlook_summary','') or '')[:80]
        print(f'  {sym:<8} tone={tone} growth={plans} risks={risks}  {outlook}')

print()
print('=== material_information (rows) ===')
mat = briefing.get('material_information',{})
for r in mat.get('rows',[])[:15]:
    print(f'  {r.get("symbol"):<8} {r.get("date"):<12} {r.get("title")[:80]}')

print()
print('=== prediction_accuracy ===')
pa = briefing.get('prediction_accuracy',{})
print(json.dumps(pa, indent=2, default=str)[:1500])

print()
print('=== overnight ===')
ov = briefing.get('overnight',{})
print(json.dumps(ov, indent=2, default=str)[:1500])

print()
print('=== scored_sentiment ===')
ss = briefing.get('scored_sentiment',{})
print(json.dumps(ss, indent=2, default=str)[:1500])

print()
print('=== regime ===')
reg = briefing.get('regime',{})
print(json.dumps(reg, indent=2, default=str)[:800])

print()
print('=== universe_movers ===')
um = briefing.get('universe_movers',{})
print(json.dumps(um, indent=2, default=str)[:1500])
