"""Find the REAL best strategy for PSX retail.

Key constraint: with 40 bps round-trip costs, we need LOW turnover.
 - Weekly rebalance = 20% annual cost drag  → kills most strategies
 - Monthly rebalance = 4.8% annual cost drag → workable
 - Signal-gated entries (~10/year) = 4% cost drag → best

Test monthly + gated approaches.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from data.store import load_ohlcv
from config.universe import symbols


# Build wide close + wide open frames
closes, opens = {}, {}
for s in symbols():
    d = load_ohlcv(s).sort_values("date").set_index("date")
    closes[s] = d["close"]
    opens[s]  = d["open"]
wide = pd.DataFrame(closes).ffill()
ow   = pd.DataFrame(opens).ffill()
wide.index = pd.to_datetime(wide.index)
ow.index   = pd.to_datetime(ow.index)

lr = np.log(wide).diff()
r  = wide.pct_change()

COST_RT = 0.004  # 40 bps round-trip


def simulate_with_costs(weights: pd.DataFrame, r: pd.DataFrame, cost: float) -> pd.Series:
    """Given (date, symbol) → target weights, simulate net returns.

    When weights change from row to row, we pay `cost * |delta_weight|` per symbol.
    Weight changes happen on the rebal day (applied immediately at next day's open).
    """
    w = weights.ffill().fillna(0)
    # transaction cost at each change
    dw = w.diff().abs().fillna(w.abs())   # first day: cost on initial buy
    daily_cost = dw.sum(axis=1) * (cost / 2)   # half cost on each leg (buy+sell = cost)
    # portfolio return = sum(w * r)
    gross = (w.shift(1) * r).sum(axis=1)   # weights apply from next day
    net = gross - daily_cost.shift(1).fillna(0)
    return net


def eval_rets(rets: pd.Series, label: str):
    rets = rets.dropna()
    if len(rets) < 60:
        print(f"  {label}: insufficient data"); return
    cum = (1 + rets).cumprod()
    yrs = len(rets) / 252
    cagr = cum.iloc[-1] ** (1 / yrs) - 1
    sh = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    ds = rets[rets < 0]
    so = rets.mean() / ds.std() * np.sqrt(252) if len(ds) and ds.std() > 0 else 0
    dd = (cum / cum.cummax() - 1).min()
    cal = cagr / abs(dd) if dd < 0 else 99
    print(f"  {label:60s}  CAGR {cagr:+.1%}  Sh {sh:.2f}  So {so:.2f}  Cal {cal:.2f}  DD {dd:.1%}")


# --------------------------------------------------------------------
# 1) Buy and hold (baseline)
# --------------------------------------------------------------------
print("\n=== Baselines ===")
w = pd.DataFrame(1.0 / len(wide.columns),
                 index=wide.index, columns=wide.columns)
eval_rets(simulate_with_costs(w, r, COST_RT),
          "Buy & hold equal-weight (set once)")


# --------------------------------------------------------------------
# 2) Monthly rotation — momentum
# --------------------------------------------------------------------
print("\n=== Monthly rotations ===")

def monthly_mom(window: int, top_n: int, vol_cap: float | None = None,
                market_filter: bool = False):
    mom = lr.rolling(window).sum()
    vol = lr.rolling(20).std()
    rebal = lr.resample("ME").last().index
    w = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal):
        if dt not in mom.index: continue
        if market_filter:
            # Skip if universe mom < 0
            if mom.loc[dt].mean() < 0:
                continue
        s = mom.loc[dt].copy()
        if vol_cap is not None:
            vr = vol.loc[dt].rank(pct=True)
            s = s.where(vr <= vol_cap)
        s = s.dropna()
        if len(s) < top_n: continue
        top = s.nlargest(top_n).index.tolist()
        start = dt + pd.Timedelta(days=1)
        end = rebal[i + 1] if i + 1 < len(rebal) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            w.loc[mask, sym] = 1.0 / top_n
    return simulate_with_costs(w, r, COST_RT)

for win in [50, 100, 150]:
    for n in [3, 5]:
        eval_rets(monthly_mom(win, n),
                  f"Monthly: top-{n} by {win}d mom")

for win in [100, 150]:
    eval_rets(monthly_mom(win, 3, vol_cap=0.7),
              f"Monthly: top-3 by {win}d mom, vol-rank<70%")

for win in [100, 150]:
    eval_rets(monthly_mom(win, 3, vol_cap=0.7, market_filter=True),
              f"Monthly: top-3 by {win}d mom, vol<70%, market>0 filter")


# --------------------------------------------------------------------
# 3) Quarterly rotation  (cost drag = only 1.6% / year)
# --------------------------------------------------------------------
print("\n=== Quarterly rotations ===")

def quarterly_mom(window: int, top_n: int, vol_cap: float | None = None):
    mom = lr.rolling(window).sum()
    vol = lr.rolling(20).std()
    rebal = lr.resample("QE").last().index
    w = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal):
        if dt not in mom.index: continue
        s = mom.loc[dt].copy()
        if vol_cap is not None:
            vr = vol.loc[dt].rank(pct=True)
            s = s.where(vr <= vol_cap)
        s = s.dropna()
        if len(s) < top_n: continue
        top = s.nlargest(top_n).index.tolist()
        start = dt + pd.Timedelta(days=1)
        end = rebal[i + 1] if i + 1 < len(rebal) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            w.loc[mask, sym] = 1.0 / top_n
    return simulate_with_costs(w, r, COST_RT)

for win in [100, 150, 200]:
    for n in [3, 5]:
        eval_rets(quarterly_mom(win, n),
                  f"Quarterly: top-{n} by {win}d mom")

for win in [100, 150]:
    eval_rets(quarterly_mom(win, 3, vol_cap=0.7),
              f"Quarterly: top-3 by {win}d mom, vol-rank<70%")


# --------------------------------------------------------------------
# 4) Signal-gated: only enter when crossing a strong threshold
#    (event-driven, hold until signal fades)
# --------------------------------------------------------------------
print("\n=== Event-driven (enter on breakout, hold with trailing stop) ===")
def breakout_strat(breakout_win: int = 50, trail_stop: float = 0.10,
                   max_positions: int = 5):
    """Enter: close breaks above `breakout_win`-day high.
       Exit:  trailing-stop hit, or close drops below 50d SMA.
    """
    sma50 = wide.rolling(50).mean()
    hi    = wide.rolling(breakout_win).max().shift(1)
    pos = {}   # sym -> entry_px, peak
    w = pd.DataFrame(0.0, index=wide.index, columns=wide.columns)
    for dt in wide.index:
        row = wide.loc[dt]
        # Check exits first
        for sym in list(pos.keys()):
            if pd.isna(row[sym]): continue
            pos[sym]["peak"] = max(pos[sym]["peak"], row[sym])
            # Trailing stop
            if row[sym] < pos[sym]["peak"] * (1 - trail_stop):
                del pos[sym]; continue
            # Below 50d SMA
            if pd.notna(sma50.loc[dt, sym]) and row[sym] < sma50.loc[dt, sym]:
                del pos[sym]; continue
        # Consider entries
        free = max_positions - len(pos)
        if free > 0:
            broke = row[(row > hi.loc[dt]) & (~row.index.isin(pos.keys()))]
            for sym in broke.nlargest(free).index:
                pos[sym] = {"entry": row[sym], "peak": row[sym]}
        # Write weights
        if pos:
            each = 1.0 / max_positions
            for sym in pos:
                w.loc[dt, sym] = each
    return simulate_with_costs(w, r, COST_RT)

for bw in [30, 50, 100]:
    for ts in [0.08, 0.12]:
        eval_rets(breakout_strat(bw, ts, 5),
                  f"Breakout-{bw}d, trail-stop {int(ts*100)}%, max-5 pos")


# --------------------------------------------------------------------
# 5) BEST so far + LLM/sentiment/macro overlay simulation
# --------------------------------------------------------------------
print("\n=== Quarterly + monthly check-in (low turnover, hybrid) ===")
# Quarterly set-and-forget at top-3 100d momentum, but DROP positions breaking -10% trailing stop
def quarterly_with_stops(window=100, top_n=3, trail_stop=0.10):
    mom = lr.rolling(window).sum()
    rebal = lr.resample("QE").last().index
    holdings = {}   # sym -> peak_px
    w = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    rebal_set = set(rebal)
    last_rebal = None
    for dt in lr.index:
        row = wide.loc[dt] if dt in wide.index else None
        if row is None: continue
        # trailing stop check per position
        for sym in list(holdings.keys()):
            if pd.isna(row[sym]): continue
            holdings[sym] = max(holdings[sym], row[sym])
            if row[sym] < holdings[sym] * (1 - trail_stop):
                del holdings[sym]
        # on rebal date → refresh the book
        if dt in rebal_set:
            s = mom.loc[dt].dropna() if dt in mom.index else None
            if s is not None and len(s) >= top_n:
                top = s.nlargest(top_n).index.tolist()
                holdings = {sym: float(wide.loc[dt, sym]) for sym in top
                            if pd.notna(wide.loc[dt, sym])}
        if holdings:
            wt = 1.0 / top_n
            for sym in holdings:
                w.loc[dt, sym] = wt
    return simulate_with_costs(w, r, COST_RT)

for win in [50, 100, 150]:
    for ts in [0.08, 0.12, 0.15]:
        eval_rets(quarterly_with_stops(win, 3, ts),
                  f"Quarterly top-3 {win}d, {int(ts*100)}% trail stop")

print("\nReminder: Buy & Hold = CAGR +19.6%, Sharpe 0.88, DD -32.3%")
print("To beat:  Sharpe > 0.88 AND DD < 32% (ideally with similar/better CAGR)")
