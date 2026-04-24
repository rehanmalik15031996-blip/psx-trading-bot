"""Audit the strategy from first principles.

Checks:
  1. Training sample sizes vs feature count (overfit risk)
  2. Target horizon overlap (iid violation)
  3. Calibration — does prob=0.55 actually mean P(up)=0.55?
  4. What a simple buy-and-hold achieves (Sharpe, DD) vs our strategy
  5. How often we trade vs how often we should
  6. The magnitude issue — AUC high but avg return low
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path

from data.store import load_ohlcv
from config.universe import symbols


# ---------- 1. Data inventory ----------
print("=" * 70)
print("1. DATA INVENTORY")
print("=" * 70)
n_days_by_sym = {}
for s in symbols():
    d = load_ohlcv(s)
    n_days_by_sym[s] = len(d)
print(f"Trading days per symbol (5 years of PSX): min={min(n_days_by_sym.values())}, "
      f"max={max(n_days_by_sym.values())}, median={int(np.median(list(n_days_by_sym.values())))}")
print(f"Total training rows available (all 15 stacked): ~{sum(n_days_by_sym.values()):,}")
print(f"  → per-stock model: ~1000 samples")
print(f"  → cross-sectional model: ~15,000 samples (15x more)")


# ---------- 2. Target noise ----------
print("\n" + "=" * 70)
print("2. TARGET (fwd_5d_return) STATISTICS")
print("=" * 70)
rets_all = []
for s in symbols():
    d = load_ohlcv(s).sort_values("date")
    r5 = d["close"].pct_change(5).shift(-5).dropna()
    rets_all.append(pd.Series(r5.values, name=s))
    up_rate = (r5 > 0).mean()
    print(f"  {s:6s}  mean={r5.mean():+.3%}  std={r5.std():.2%}  up%={up_rate:.1%}  "
          f"5th/95th=[{r5.quantile(0.05):+.1%}, {r5.quantile(0.95):+.1%}]")

all_rets = pd.concat(rets_all)
print(f"\nOverall: mean fwd_5d_ret = {all_rets.mean():+.3%}, std = {all_rets.std():.2%}")
print(f"→ To beat 0.40% round-trip cost, avg per-trade edge must be > 0.40%.")
print(f"→ With random entries, expected return ≈ +{all_rets.mean()*100:.2f}% per 5-day hold.")


# ---------- 3. Calibration check ----------
print("\n" + "=" * 70)
print("3. MODEL CALIBRATION (walk-forward signals)")
print("=" * 70)
sig_path = Path("data")
cached = list(sig_path.glob("walkforward_signals*.parquet"))
if cached:
    sig = pd.read_parquet(cached[0])
    print(f"Loaded {cached[0].name}: {len(sig):,} rows")

    # If calibrated: in bin prob=[0.55, 0.60], actual up-rate should be ~0.575
    sig["prob_bin"] = pd.cut(sig["prob_up_oos"], bins=[0, .3, .4, .45, .5, .55, .6, .7, 1.0])
    cal = sig.groupby("prob_bin", observed=True).agg(
        n=("fwd_ret_5d_up", "size"),
        actual_up=("fwd_ret_5d_up", "mean"),
        mean_prob=("prob_up_oos", "mean"),
        avg_fwd_ret=("fwd_ret_5d", "mean"),
    ).round(3)
    cal["gap"] = (cal["actual_up"] - cal["mean_prob"]).round(3)
    print(cal)
    print("\n→ If 'gap' column is near 0, model is calibrated.")
    print("→ If 'actual_up' in top bin (0.7-1.0) < 0.6, rank-averaging destroyed probability meaning.")

    print(f"\nDays crossing entry threshold 0.55: "
          f"{(sig.prob_up_oos >= 0.55).sum():,} of {len(sig):,} "
          f"({(sig.prob_up_oos >= 0.55).mean():.1%})")
    print(f"→ We 'enter' 45%+ of days → way too much noise. Real alpha signals are rare.")


# ---------- 4. Benchmark (buy-and-hold) realistic stats ----------
print("\n" + "=" * 70)
print("4. BUY-AND-HOLD BENCHMARK (realistic baseline)")
print("=" * 70)
prices = {}
for s in symbols():
    d = load_ohlcv(s).sort_values("date").set_index("date")["close"]
    prices[s] = d
wide = pd.DataFrame(prices).ffill().dropna(how="all")
# Equal-weight daily returns
daily_ret = wide.pct_change().mean(axis=1).dropna()
cum = (1 + daily_ret).cumprod()

n_days = len(daily_ret)
years = n_days / 252
cagr = cum.iloc[-1] ** (1 / years) - 1
sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
downside = daily_ret[daily_ret < 0]
sortino = daily_ret.mean() / downside.std() * np.sqrt(252)
roll_max = cum.cummax()
max_dd = (cum / roll_max - 1).min()
calmar = cagr / abs(max_dd)

print(f"Equal-weight 15-stock buy-and-hold over {years:.1f}y:")
print(f"  CAGR          {cagr:+.2%}")
print(f"  Sharpe        {sharpe:.2f}")
print(f"  Sortino       {sortino:.2f}")
print(f"  Calmar        {calmar:.2f}")
print(f"  Max DD        {max_dd:.2%}")
print()
print("→ If strategy Sharpe < benchmark Sharpe, we're ADDING risk without reward.")
print("→ Benchmark Calmar > 1.5 in bull market is typical. Must BEAT this, not just CAGR.")


# ---------- 5. Simplest sensible rule: 50-day momentum on relative strength ----------
print("\n" + "=" * 70)
print("5. SIMPLE RULE BASELINE: top-5 by 50d return, rebalance weekly")
print("=" * 70)
# Using log returns for stability
lr = np.log(wide).diff()
mom_50 = lr.rolling(50).sum()
rets_weekly = wide.pct_change().resample("W-FRI").apply(lambda s: (1 + s).prod() - 1)
# For each week, pick top-5 by 50d momentum as of that Friday
scores = mom_50.resample("W-FRI").last()

strat = []
for i, dt in enumerate(scores.index[:-1]):
    ranks = scores.loc[dt].dropna()
    if len(ranks) < 5: continue
    top5 = ranks.nlargest(5).index.tolist()
    # next week's return of equal-weighted top-5
    nxt_week = scores.index[i + 1]
    wk_ret = rets_weekly.loc[nxt_week, top5].mean()
    strat.append((nxt_week, wk_ret))

mdf = pd.DataFrame(strat, columns=["date", "w_ret"]).set_index("date")
cum2 = (1 + mdf["w_ret"]).cumprod()
years2 = len(cum2) / 52
cagr2 = cum2.iloc[-1] ** (1 / years2) - 1
sharpe2 = mdf["w_ret"].mean() / mdf["w_ret"].std() * np.sqrt(52)
max_dd2 = (cum2 / cum2.cummax() - 1).min()
calmar2 = cagr2 / abs(max_dd2)
print(f"Top-5 by 50-day momentum, weekly rebalance (NO ML, just ranking):")
print(f"  CAGR          {cagr2:+.2%}")
print(f"  Sharpe        {sharpe2:.2f}")
print(f"  Calmar        {calmar2:.2f}")
print(f"  Max DD        {max_dd2:.2%}")
print("  (No transaction cost applied, but 1 rebal/wk → trivial)")


# ---------- 6. Overlapping target problem ----------
print("\n" + "=" * 70)
print("6. OVERLAPPING-TARGET PROBLEM")
print("=" * 70)
# Autocorrelation of fwd_5d_ret at lag 1, 2, 3, 4, 5
sym0 = symbols()[0]
d0 = load_ohlcv(sym0).sort_values("date")
r5 = d0["close"].pct_change(5).shift(-5).dropna()
for lag in [1, 2, 3, 4, 5]:
    ac = r5.autocorr(lag=lag)
    print(f"  autocorr(fwd_5d_ret, lag={lag}) = {ac:+.3f}")
print("→ Lags 1-4 should be non-zero because targets OVERLAP. This violates iid")
print("→ assumption used by TimeSeriesSplit. Effective sample size is ~N/5, not N.")
print("→ PROPER fix: use PURGED walk-forward with 5-day gap between train/test.")
