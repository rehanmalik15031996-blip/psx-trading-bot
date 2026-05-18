"""Compute actual per-stock and per-sector returns May 11 -> May 14."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from config.universe import UNIVERSE

START = date(2026, 5, 11)
END   = date(2026, 5, 15)


def _ret(sym: str) -> tuple[float, float, float] | None:
    p = Path("data/ohlcv") / f"{sym}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    sub = df[(df["date"] >= START) & (df["date"] <= END)].sort_values("date")
    if len(sub) < 2:
        return None
    p0 = float(sub.iloc[0]["close"])
    p1 = float(sub.iloc[-1]["close"])
    ret = (p1 / p0) - 1
    return p0, p1, ret


print("=" * 78)
print(f"  Actual per-stock returns: {START} (close) -> {END} (close)")
print(f"  (Monday close -> Friday close, the 5-day work-week)")
print("=" * 78)
rows = []
for u in UNIVERSE:
    out = _ret(u.symbol)
    if out is None:
        continue
    p0, p1, r = out
    rows.append((u.symbol, u.sector, p0, p1, r * 100))

df = pd.DataFrame(rows, columns=["symbol", "sector", "p_start", "p_end", "ret_pct"])
df = df.sort_values("ret_pct")

print("\n=== Worst 8 ===")
print(df.head(8).to_string(index=False))
print("\n=== Best 8 ===")
print(df.tail(8).to_string(index=False))

print("\n=== Sector aggregates (mean return %) ===")
sec = df.groupby("sector")["ret_pct"].agg(["mean", "count"]).sort_values("mean")
print(sec.to_string())

# Compute KSE-100 return  
k = pd.read_parquet("data/macro/kse100.parquet")
k["date"] = pd.to_datetime(k["date"]).dt.date
sub = k[(k["date"] >= START) & (k["date"] <= END)].sort_values("date")
if len(sub) >= 2:
    k_ret = (sub.iloc[-1]["kse100_close"] / sub.iloc[0]["kse100_close"]) - 1
    print(f"\nKSE-100: {sub.iloc[0]['kse100_close']:.0f} -> "
          f"{sub.iloc[-1]['kse100_close']:.0f}  ({k_ret*100:+.2f}%)")

print("\n=== Macro moves during the week ===")
for name, f, col in [
    ("Brent",    "brent.parquet",    "value"),
    ("WTI",      "wti.parquet",      "value"),
    ("Copper",   "copper.parquet",   "value"),
    ("Gold",     "gold.parquet",     "value"),
    ("BTC",      "btc.parquet",      "value"),
    ("USDPKR",   "usdpkr.parquet",   "value"),
    ("Cotton",   "cotton.parquet",   "value"),
]:
    p = Path("data/macro") / f
    if not p.exists():
        continue
    dfm = pd.read_parquet(p)
    dfm["date"] = pd.to_datetime(dfm["date"]).dt.date
    sub = dfm[(dfm["date"] >= START) & (dfm["date"] <= END)].sort_values("date")
    if len(sub) >= 2:
        s = float(sub.iloc[0][col]); e = float(sub.iloc[-1][col])
        print(f"  {name:<8} {s:>9.2f} -> {e:>9.2f}  ({(e/s-1)*100:+.2f}%)")

# What we recommended on May 10 (Sunday — pre-Monday call)
print("\n" + "=" * 78)
print("  WHAT WE SAID before the week opened (May 10 strategist file)")
print("=" * 78)
p = Path("data/_strategist/2026-05-10.json")
if p.exists():
    body = json.loads(p.read_text(encoding="utf-8"))
    print(f"  Headline:  {body.get('headline','')[:120]}")
    print(f"  Stance:    {body.get('risk_stance')}  "
          f"(conv {body.get('conviction')})")
    narrative = (body.get('narrative') or '')[:400]
    if narrative:
        print(f"  Narrative: {narrative}")
    actions = (body.get('actions') or [])
    bucket_counts = {}
    for a in actions:
        bucket_counts.setdefault(a.get('bucket','?'), 0)
        bucket_counts[a.get('bucket','?')] += 1
    print(f"  Bucket distribution: {bucket_counts}")
    print(f"  Named actions (with symbol):")
    for a in actions[:12]:
        if a.get('symbol'):
            tw = a.get('target_weight_pct')
            tw_s = f"{tw:.1f}%" if isinstance(tw, (int, float)) else "—"
            print(f"    {a['symbol']:<8} {a.get('bucket','?'):<6} "
                  f"{a.get('conviction','?'):<8} weight={tw_s:<6}  "
                  f"{(a.get('reason') or '')[:65]}")

# What we said on May 8 (Friday close — last reading before the weekend)
print("\n" + "=" * 78)
print("  WHAT WE SAID on Fri May 8 (last call before the down-week)")
print("=" * 78)
p = Path("data/_strategist/2026-05-08.json")
if p.exists():
    body = json.loads(p.read_text(encoding="utf-8"))
    print(f"  Headline:  {body.get('headline','')[:120]}")
    print(f"  Stance:    {body.get('risk_stance')}  "
          f"(conv {body.get('conviction')})")
    narrative = (body.get('narrative') or '')[:300]
    if narrative:
        print(f"  Narrative: {narrative}")
    actions = body.get('actions') or []
    named = [a for a in actions if a.get('symbol')]
    print(f"  Bucket distribution: ", end="")
    bc = {}
    for a in actions:
        bc.setdefault(a.get('bucket','?'), 0)
        bc[a.get('bucket','?')] += 1
    print(bc)
    print(f"  Named actions:")
    for a in named[:12]:
        tw = a.get('target_weight_pct')
        tw_s = f"{tw:.1f}%" if isinstance(tw, (int, float)) else "—"
        print(f"    {a['symbol']:<8} {a.get('bucket','?'):<6} "
              f"{a.get('conviction','?'):<8} weight={tw_s:<6}  "
              f"{(a.get('reason') or '')[:65]}")
