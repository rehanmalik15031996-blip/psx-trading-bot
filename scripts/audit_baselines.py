"""Compare simple, no-ML strategies — find the real baseline to beat.

The question: can ML add alpha on top of a well-designed rule?
If a simple rule does 25% CAGR with Sharpe 1.0, our ML needs to do BETTER.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from data.store import load_ohlcv
from config.universe import symbols

# Build wide price frame (daily close)
prices = {}
for s in symbols():
    d = load_ohlcv(s).sort_values("date").set_index("date")["close"]
    prices[s] = d
wide = pd.DataFrame(prices).ffill().dropna(how="all")
wide.index = pd.to_datetime(wide.index)
print(f"Wide price frame: {len(wide)} days, {len(wide.columns)} symbols, "
      f"{wide.index.min().date()} → {wide.index.max().date()}")

# Daily log-return and simple-return
lr = np.log(wide).diff()
r = wide.pct_change()
COST_RT = 0.004  # 40 bps round-trip to match our backtester


def eval_strategy(daily_ret: pd.Series, name: str, n_rebal: int = 0):
    daily_ret = daily_ret.dropna()
    if len(daily_ret) < 100:
        print(f"  {name}: insufficient data")
        return None
    cum = (1 + daily_ret).cumprod()
    years = len(daily_ret) / 252
    cagr = cum.iloc[-1] ** (1 / years) - 1
    sh = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    ds = daily_ret[daily_ret < 0]
    so = daily_ret.mean() / ds.std() * np.sqrt(252) if len(ds) and ds.std() > 0 else 0
    dd = (cum / cum.cummax() - 1).min()
    cal = cagr / abs(dd) if dd < 0 else 99
    # Cost-adjust by nominal turnover: assume full turnover per rebal
    if n_rebal > 0:
        cost_drag_annual = n_rebal * COST_RT
        cagr_net = (1 + cagr) / (1 + cost_drag_annual) - 1
    else:
        cagr_net = cagr
    print(f"  {name:55s}  CAGR {cagr:+.1%} ({cagr_net:+.1%} after costs)  "
          f"Sh {sh:.2f}  Cal {cal:.2f}  DD {dd:.1%}")
    return {"cagr": cagr, "cagr_net": cagr_net, "sharpe": sh, "calmar": cal}


# ------------------------------------------------------------------
# Strategy A: buy and hold equal-weight (baseline)
# ------------------------------------------------------------------
print("\n--- Baselines (no trading logic) ---")
ew = r.mean(axis=1)
eval_strategy(ew, "Equal-weight 15 stocks, buy & hold", n_rebal=0)

# "KSE-100 proxy" — simple market-cap-ish proxy: equal-weight index
# (we don't have real KSE-100 here, so we use this as rough proxy)

# ------------------------------------------------------------------
# Strategy B: cross-sectional momentum — top N by K-day return, rebal weekly
# ------------------------------------------------------------------
print("\n--- Cross-sectional momentum (weekly rebalance, top-N) ---")

def momentum_strategy(window_days: int, top_n: int, rebal_freq: str = "W-FRI",
                      min_vol_rank: float | None = None):
    """Pick top_n by window-day return, rebalance at rebal_freq."""
    mom = lr.rolling(window_days).sum()
    if min_vol_rank is not None:
        # Filter out high-vol names BEFORE momentum ranking
        vol20 = lr.rolling(20).std()
        vol_rank = vol20.rank(axis=1, pct=True)  # low rank = low vol
        # keep only names with vol_rank < min_vol_rank
        mom = mom.where(vol_rank <= min_vol_rank)
    # On each rebal date, take top-N, hold until next rebal
    rebal_dates = lr.resample(rebal_freq).last().index
    daily_rets = pd.Series(0.0, index=lr.index)
    weights = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    current = None
    for i, dt in enumerate(rebal_dates):
        # Pick top_n by momentum AS OF dt
        if dt not in mom.index: continue
        s = mom.loc[dt].dropna()
        if len(s) < top_n: continue
        top = s.nlargest(top_n).index.tolist()
        # Apply weights from dt+1 to next rebal
        start = dt + pd.Timedelta(days=1)
        end = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            weights.loc[mask, sym] = 1.0 / top_n
        current = top
    # Daily return of weighted portfolio
    port_ret = (weights * r).sum(axis=1)
    return port_ret, 52  # approx weekly rebalances per year


for window in [20, 50, 100]:
    for top_n in [3, 5]:
        rets, n_reb = momentum_strategy(window, top_n)
        eval_strategy(rets, f"Top-{top_n} by {window}d momentum, weekly", n_rebal=n_reb)

# ------------------------------------------------------------------
# Strategy C: dual momentum — only hold when 20d return > 0
# ------------------------------------------------------------------
print("\n--- Dual momentum (skip market when negative) ---")

def dual_momentum(window: int, top_n: int, skip_neg_market: bool = True):
    mom = lr.rolling(window).sum()
    rebal_dates = lr.resample("W-FRI").last().index
    weights = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal_dates):
        if dt not in mom.index: continue
        # Equal-weight universe's 20d return — if negative, go to cash
        if skip_neg_market and lr.loc[:dt].tail(window).sum().mean() < 0:
            continue
        s = mom.loc[dt].dropna()
        if len(s) < top_n: continue
        top = s.nlargest(top_n).index.tolist()
        start = dt + pd.Timedelta(days=1)
        end = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            weights.loc[mask, sym] = 1.0 / top_n
    port_ret = (weights * r).sum(axis=1)
    return port_ret

for window in [50, 100]:
    for top_n in [3, 5]:
        rets = dual_momentum(window, top_n, skip_neg_market=True)
        eval_strategy(rets, f"Dual mom: top-{top_n} by {window}d, skip neg market", n_rebal=52)

# ------------------------------------------------------------------
# Strategy D: low-vol top momentum (quality filter)
# ------------------------------------------------------------------
print("\n--- Low-vol + momentum (quality filter) ---")
for window in [50, 100]:
    rets, n_reb = momentum_strategy(window, top_n=3, min_vol_rank=0.7)
    eval_strategy(rets, f"Top-3 by {window}d mom, vol-rank < 70%", n_rebal=52)


# ------------------------------------------------------------------
# Strategy E: Mean reversion on oversold (RSI-proxy)
# ------------------------------------------------------------------
print("\n--- Mean reversion (buy dips, weekly) ---")
# Oversold = strongly negative 5-day return. Hold 1 week.
def mean_rev(lookback: int, bottom_n: int = 3):
    short_ret = lr.rolling(lookback).sum()
    rebal_dates = lr.resample("W-FRI").last().index
    weights = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal_dates):
        if dt not in short_ret.index: continue
        s = short_ret.loc[dt].dropna()
        if len(s) < bottom_n: continue
        bottom = s.nsmallest(bottom_n).index.tolist()
        start = dt + pd.Timedelta(days=1)
        end = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in bottom:
            weights.loc[mask, sym] = 1.0 / bottom_n
    return (weights * r).sum(axis=1)

for lb in [3, 5, 10]:
    rets = mean_rev(lb, 3)
    eval_strategy(rets, f"Buy bottom-3 by {lb}d return, weekly hold", n_rebal=52)


# ------------------------------------------------------------------
# Strategy F: Combined — momentum + mean-rev + market filter
# ------------------------------------------------------------------
print("\n--- Combined signals (momentum + macro filter) ---")
def combined_strategy(mom_window=50, top_n=3, vol_cap=0.70, skip_bear=True):
    """Top-N by mom, filtered by low vol, skip if market 50d return < 0."""
    mom = lr.rolling(mom_window).sum()
    vol20 = lr.rolling(20).std()
    vol_rank = vol20.rank(axis=1, pct=True)
    rebal_dates = lr.resample("W-FRI").last().index
    weights = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal_dates):
        if dt not in mom.index: continue
        # Market filter
        market_mom = mom.loc[dt].dropna().mean()
        if skip_bear and market_mom < 0:
            continue
        s = mom.loc[dt].copy()
        s = s.where(vol_rank.loc[dt] <= vol_cap)
        s = s.dropna()
        if len(s) < top_n: continue
        top = s.nlargest(top_n).index.tolist()
        start = dt + pd.Timedelta(days=1)
        end = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            weights.loc[mask, sym] = 1.0 / top_n
    return (weights * r).sum(axis=1)

for mw, tn, vc in [(50, 3, 0.70), (100, 3, 0.70), (50, 5, 0.70), (60, 3, 0.80)]:
    rets = combined_strategy(mw, tn, vc, skip_bear=True)
    eval_strategy(rets,
                  f"Combo: top-{tn} {mw}d mom, vol<{int(vc*100)}pct, market filter",
                  n_rebal=52)

print("\nKey question:  does ML beat the BEST simple rule above, net of costs?")
