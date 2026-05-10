"""Gather all market data for strategist reasoning."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
import pandas as pd

# News
news_path = Path('data/news/scored_news.parquet')
if news_path.exists():
    df = pd.read_parquet(news_path)
    df = df.sort_values('published_at', ascending=False).head(20)
    print('=== LATEST SCORED NEWS (most recent first) ===')
    for _, r in df.iterrows():
        score = float(r.get('sentiment', 0) or 0)
        title = str(r.get('title', ''))[:85]
        pub   = str(r.get('published_at', ''))[:10]
        cat   = str(r.get('category', ''))
        syms  = str(r.get('affected_symbols', ''))[:20]
        liner = str(r.get('one_liner', ''))[:70]
        print(f'  [{score:+.2f}] {pub} [{cat}] {syms}')
        print(f'         {title}')
        if liner: print(f'         -> {liner}')

# All predictions
pred_data = json.loads(
    Path('data/predictions_log.json').read_bytes().decode('utf-8', 'replace'))
preds = pred_data.get('predictions') or []

def _pred_date(p):
    return (p.get('generated_at') or '')[:10]

as_of = max(_pred_date(p) for p in preds) if preds else '?'
latest = [p for p in preds if _pred_date(p) == as_of]
print()
print(f'=== ALL {len(latest)} PREDICTIONS (as_of {as_of}) ===')
sorted_p = sorted(latest, key=lambda x: -(x.get('expected_return_5d_mid_pct') or 0))
for p in sorted_p:
    sym    = p.get('symbol', '?')
    dirn   = str(p.get('direction', '?'))[:7]
    action = str(p.get('suggested_action', '?'))
    pconv  = str(p.get('conviction', '?'))[:4]
    ret    = p.get('expected_return_5d_mid_pct') or 0
    rat    = str(p.get('rationale') or '')[:60]
    print(f'  {sym:<8} {dirn:<8} {action:<5} {pconv:<4} {ret:+.1f}%  {rat}')

# KSE100 recent
kse_path = Path('data/macro/kse100.parquet')
if kse_path.exists():
    kdf = pd.read_parquet(kse_path).tail(10)
    print()
    print('=== KSE100 RECENT PRICE ACTION ===')
    cols = [c for c in ['date', 'close', 'ret_1d', 'ret_5d', 'ret_21d'] if c in kdf.columns]
    print(kdf[cols].to_string())

# Policy rate
rate_path = Path('data/macro/_policy_rate_history.json')
if rate_path.exists():
    rates = json.loads(rate_path.read_bytes().decode('utf-8', 'replace'))
    last = rates[-1] if rates else {}
    print()
    print(f'=== POLICY RATE: {last.get("rate_pct","?")}%'
          f' as of {last.get("effective_date","?")} ===')
    if len(rates) >= 2:
        prev = rates[-2]
        delta = (last.get('rate_pct', 0) or 0) - (prev.get('rate_pct', 0) or 0)
        print(f'  Previous: {prev.get("rate_pct","?")}%  Change: {delta:+.0f}bps')

# Director reports excerpt
fin_path = Path('data/_health/financial_results.json')
if fin_path.exists():
    fin = json.loads(fin_path.read_bytes().decode('utf-8', 'replace'))
    payload = fin.get('payload') or {}
    reports = payload.get('reports') or []
    if reports:
        print()
        print('=== RECENT DIRECTOR REPORTS ===')
        for r in reports[:5]:
            sym = r.get('symbol', '?')
            outlook = str(r.get('management_outlook', ''))[:100]
            print(f'  {sym}: {outlook}')
