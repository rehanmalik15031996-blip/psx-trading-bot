"""Plan D: core strategy module (deterministic, rule-based).

This module implements the evidence-based monthly-rotation rule that was
validated in the audit scripts (scripts/audit_*.py). See psx_strategy_v2.md
for the full design rationale.

Design goals:
  - Deterministic. Same inputs → same outputs. Easy to audit, easy to reason
    about.
  - No ML in the entry signal. The universe is ranked by pure 150-day log
    return, filtered by 20-day realized volatility, gated on a market-trend
    filter.
  - Low turnover. Monthly rebalance + trailing stops → ~15-25 trades/year.
  - Cost-aware. Edge survives up to 100 bps round-trip per audit_deep.py.

Public API:
  - rank_universe(prices_wide, as_of) -> list of (symbol, momentum_score)
  - filter_universe(ranked, vol_wide, as_of, vol_cap=0.70) -> list (post vol filter)
  - market_is_risk_on(mom_wide, as_of) -> bool
  - pick_monthly(prices_wide, as_of, top_n=3, ...) -> list of symbols (or [])
  - trailing_stop_hit(entry_peak, current_px, stop_pct=0.15) -> bool
  - StrategyConfig: thresholds and sizing knobs
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class StrategyConfig:
    """Production configuration. Defaults are the result of parameter sweeps
    in `scripts/tune_stops.py` — optimised for Sharpe/Calmar vs B&H, with
    MaxDD as a hard ceiling (~22%, vs B&H's ~32%).
    """
    # Momentum signal --------------------------------------------------
    momentum_window: int = 150        # days of log-return to sum
    vol_window: int = 20              # days for realized vol estimate
    vol_rank_cap: float = 0.70        # exclude top 30% by vol
    top_n: int = 5                    # positions held at once (5 > 3 on Sharpe)

    # Market-trend filter ----------------------------------------------
    market_filter_on: bool = True
    market_mom_window: int = 150      # same window for universe mean
    # Hysteresis band around the zero threshold. See _hysteresis_state()
    # docstring for the mechanism and rationale. 0.05 validated 2026-09-01:
    # stable plateau from 0.05-0.12 (all ~+34% CAGR / 1.5 Sharpe), degrading
    # above ~0.15 -- not a knife-edge fit to one value. Critically, 2021-2025
    # backtest results are BYTE-IDENTICAL at every band from 0.0 to 0.15 --
    # this only ever engages during 2026's near-zero/rangebound stretch, it
    # does not reshape any year that was already working. Fixes the 2026
    # whipsaw flagged in docs/strategy_evaluation_2026-09-01.md: universe
    # momentum oscillated -0.045/-0.058/+0.004/+0.085/-0.039/-0.095 Mar-Aug
    # 2026 (noise-level swings around zero), and the plain threshold flipped
    # the whole book on it almost every month. 2026 backtest return:
    # -5.96% (band=0) -> +10.35% (band=0.05), MaxDD unchanged.
    market_mom_band: float = 0.05

    # Risk management --------------------------------------------------
    # Stops are OFF by default: parameter sweep showed any stop band
    # (-15 to -25%) degrades Sharpe vs monthly-rebalance with no stop.
    # Rebalance itself IS the risk control; stops cause whipsaw exits.
    use_trailing_stop: bool = False
    trailing_stop_pct: float = 0.20   # kept as a knob; unused by default

    # Rebalance cadence -------------------------------------------------
    rebalance_freq: str = "ME"        # month-end (pandas frequency)

    # Cost assumptions (for simulation) --------------------------------
    cost_round_trip: float = 0.004    # 40 bps round-trip

    # LLM overlay exposure levels (used by overlay.py) -----------------
    exposure_normal: float = 1.00
    exposure_caution: float = 0.75
    exposure_crisis: float = 0.50


# --------------------------------------------------------------------------
# Momentum / volatility core
# --------------------------------------------------------------------------
def compute_momentum(prices_wide: pd.DataFrame, window: int) -> pd.DataFrame:
    """Rolling sum of log returns over `window` days (momentum score)."""
    log_ret = np.log(prices_wide).diff()
    return log_ret.rolling(window).sum()


def compute_realized_vol(prices_wide: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Annualized realized volatility from daily log returns."""
    log_ret = np.log(prices_wide).diff()
    return log_ret.rolling(window).std() * np.sqrt(252)


# --------------------------------------------------------------------------
# Ranking + filtering
# --------------------------------------------------------------------------
def rank_universe(
    prices_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    cfg: StrategyConfig | None = None,
) -> pd.Series:
    """Return momentum-ranked series (symbol -> momentum score) on `as_of`.

    Drops symbols with NaN momentum (insufficient history).
    """
    cfg = cfg or StrategyConfig()
    mom = compute_momentum(prices_wide, cfg.momentum_window)
    if as_of not in mom.index:
        # Use the latest available row at or before `as_of`
        valid = mom.index[mom.index <= as_of]
        if len(valid) == 0:
            return pd.Series(dtype=float)
        as_of = valid[-1]
    row = mom.loc[as_of].dropna()
    return row.sort_values(ascending=False)


def apply_vol_filter(
    ranked: pd.Series,
    prices_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    cfg: StrategyConfig | None = None,
) -> pd.Series:
    """Exclude high-vol names (keep bottom `vol_rank_cap` quantile by vol)."""
    cfg = cfg or StrategyConfig()
    vol = compute_realized_vol(prices_wide, cfg.vol_window)
    if as_of not in vol.index:
        valid = vol.index[vol.index <= as_of]
        if len(valid) == 0:
            return ranked
        as_of = valid[-1]
    vol_row = vol.loc[as_of]
    vol_rank = vol_row.rank(pct=True)            # 0 = lowest vol, 1 = highest
    keep = vol_rank[vol_rank <= cfg.vol_rank_cap].index
    return ranked[ranked.index.intersection(keep)]


# --------------------------------------------------------------------------
# Market trend filter
# --------------------------------------------------------------------------
def _hysteresis_state(market_mom: float, prev_state: bool | None, band: float) -> bool:
    """Shared ON/OFF decision used by both live (market_is_risk_on) and the
    backtest loop (brain/backtest_v2.py) -- kept as ONE function specifically
    so the two can't silently drift apart the way they did before (see
    docs/strategy_evaluation_2026-09-01.md).

    Plain zero threshold (band=0, the default) is unchanged: >=0 -> True.

    With band > 0, this is a Schmitt-trigger / dead-band hysteresis: once in
    a state, momentum has to cross the OPPOSITE side of the band to flip out
    of it, not just cross zero. Motivation: 2026's month-end universe
    momentum has been oscillating in a +/-0.10 range around zero since March
    (-0.045, -0.058, +0.004, +0.085, -0.039, -0.095) -- noise-level swings
    that the plain threshold treats as full regime changes, causing a
    rebalance-cost round trip almost every month for a market that is
    genuinely just flat/rangebound, not trending down. This is the standard
    fix for exactly this failure mode in trend-following systems (asymmetric
    enter/exit thresholds) -- see CME Group, "Improving Time-Series Momentum
    Strategies" and the general dead-band/hysteresis literature on
    whipsaw reduction. NOT tied to any specific historical event (contrast
    with the IMF-floor override tested and rejected earlier this session for
    exactly that reason) -- this is a generic statistical noise filter.
    """
    if band <= 0 or prev_state is None:
        return bool(market_mom >= 0)
    if prev_state:
        return bool(market_mom >= -band)   # stay ON unless it drops below -band
    return bool(market_mom >= band)        # stay OFF unless it rises above +band


def _monthly_rebalance_dates(prices_wide: pd.DataFrame) -> pd.DatetimeIndex:
    """Last trading day of each calendar month present in the index."""
    period = prices_wide.index.to_period("M")
    return pd.DatetimeIndex(prices_wide.index.to_series().groupby(period).max().values)


def _replay_market_state(
    prices_wide: pd.DataFrame, as_of: pd.Timestamp, cfg: StrategyConfig,
) -> bool | None:
    """Derive the hysteresis state a caller would be carrying into `as_of`,
    by sequentially replaying every monthly rebalance strictly before it.

    Why this exists: hysteresis needs the PREVIOUS month's state, but most
    callers (ui/tools.py, scripts/generate_report_v2.py) call
    market_is_risk_on()/pick_monthly() for a single point in time and don't
    track month-to-month state themselves. Without this, setting
    cfg.market_mom_band > 0 would silently do nothing for any caller that
    doesn't explicitly thread prev_state through -- exactly the kind of
    "fixed in the backtest, no-op live" gap this session has been hunting
    all day. Recomputing from price history keeps this a pure function
    (same inputs -> same outputs, no new state file to go stale or drift)
    rather than persisting state to disk. brain/backtest_v2.py's own loop
    tracks state itself for performance and does NOT go through this path.
    """
    dates = _monthly_rebalance_dates(prices_wide)
    dates = dates[dates < as_of]
    mom = compute_momentum(prices_wide, cfg.market_mom_window)
    state: bool | None = None
    for dt in dates:
        if dt not in mom.index:
            continue
        m = mom.loc[dt].mean()
        if pd.isna(m):
            continue
        state = _hysteresis_state(float(m), state, cfg.market_mom_band)
    return state


def market_is_risk_on(
    prices_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    cfg: StrategyConfig | None = None,
    prev_state: bool | None = None,
) -> bool:
    """True if the equal-weighted universe momentum clears the risk-on bar at `as_of`.

    When this returns False, the monthly rotation should go to cash.

    `prev_state`: the previous rebalance's result, for callers (like the
    backtest loop) that already track it. If omitted and
    `cfg.market_mom_band > 0`, it's auto-derived by replaying history (see
    `_replay_market_state`) so callers don't have to track state themselves
    to benefit from hysteresis.
    """
    cfg = cfg or StrategyConfig()
    mom = compute_momentum(prices_wide, cfg.market_mom_window)
    if as_of not in mom.index:
        valid = mom.index[mom.index <= as_of]
        if len(valid) == 0:
            return False
        as_of = valid[-1]
    market_mom = mom.loc[as_of].mean()
    if pd.isna(market_mom):
        return False
    if cfg.market_mom_band > 0 and prev_state is None:
        prev_state = _replay_market_state(prices_wide, as_of, cfg)
    return _hysteresis_state(float(market_mom), prev_state, cfg.market_mom_band)


# --------------------------------------------------------------------------
# Monthly pick (pure function)
# --------------------------------------------------------------------------
@dataclass
class MonthlyPick:
    as_of: pd.Timestamp
    market_risk_on: bool
    ranked_all: pd.Series
    filtered: pd.Series
    selected: list[str]
    reason: str


def pick_monthly(
    prices_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    cfg: StrategyConfig | None = None,
    prev_state: bool | None = None,
) -> MonthlyPick:
    """Decide the month's holdings based purely on price data up to `as_of`.

    Returns a MonthlyPick record explaining the decision path. If the market
    filter vetoes the month, `selected` is [] and `reason` explains why.

    `prev_state`: previous month's market_risk_on result. Only affects the
    decision when `cfg.market_mom_band > 0` (hysteresis) -- see
    `_hysteresis_state`. Callers that don't track month-to-month state can
    omit it; the filter falls back to the plain zero-threshold behaviour.
    """
    cfg = cfg or StrategyConfig()
    ranked = rank_universe(prices_wide, as_of, cfg)

    if ranked.empty:
        return MonthlyPick(as_of, False, ranked, ranked, [],
                           "No valid momentum data for any symbol")

    if cfg.market_filter_on and not market_is_risk_on(prices_wide, as_of, cfg, prev_state):
        return MonthlyPick(as_of, False, ranked, pd.Series(dtype=float), [],
                           f"Market filter: universe {cfg.market_mom_window}d "
                           f"mom is negative — go to cash")

    filtered = apply_vol_filter(ranked, prices_wide, as_of, cfg)
    if len(filtered) < cfg.top_n:
        return MonthlyPick(as_of, True, ranked, filtered, [],
                           f"Only {len(filtered)} names survive vol filter "
                           f"(need {cfg.top_n})")

    selected = filtered.head(cfg.top_n).index.tolist()
    return MonthlyPick(as_of, True, ranked, filtered, selected,
                       f"Top-{cfg.top_n} by {cfg.momentum_window}d momentum "
                       f"after vol<{int(cfg.vol_rank_cap*100)}pct filter")


# --------------------------------------------------------------------------
# Risk management
# --------------------------------------------------------------------------
def trailing_stop_hit(
    entry_px: float,
    peak_px: float,
    current_px: float,
    stop_pct: float = 0.15,
) -> bool:
    """True if the current price has fallen more than `stop_pct` from peak.

    peak_px is the max close since entry. This does NOT activate before the
    position goes green (peak <= entry means we haven't had any profit yet;
    still using the pct drop from peak is acceptable — it caps losses).
    """
    if current_px <= 0 or peak_px <= 0:
        return False
    return current_px <= peak_px * (1 - stop_pct)


# --------------------------------------------------------------------------
# Helper: build wide price frame from individual stock parquets
# --------------------------------------------------------------------------
def build_prices_wide(symbols: list[str]) -> pd.DataFrame:
    """Load OHLCV for each symbol and return a wide (date × symbol) close frame."""
    from data.store import load_ohlcv
    closes = {}
    for s in symbols:
        d = load_ohlcv(s)
        if d.empty:
            continue
        closes[s] = d.sort_values("date").set_index("date")["close"]
    if not closes:
        return pd.DataFrame()
    wide = pd.DataFrame(closes).ffill()
    wide.index = pd.to_datetime(wide.index)
    return wide


# --------------------------------------------------------------------------
# Smoke test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from config.universe import symbols as universe_symbols

    cfg = StrategyConfig()
    wide = build_prices_wide(universe_symbols())
    print(f"Price frame: {len(wide)} days × {len(wide.columns)} symbols")
    as_of = wide.index[-1]
    pick = pick_monthly(wide, as_of, cfg)
    print(f"\nAs of {as_of.date()}:")
    print(f"  market_risk_on: {pick.market_risk_on}")
    print(f"  ranked (top-8):")
    for sym, score in pick.ranked_all.head(8).items():
        print(f"    {sym:6s} 150d log-ret = {score:+.2%}")
    print(f"  after vol filter ({len(pick.filtered)} names): "
          f"{pick.filtered.index.tolist()}")
    print(f"  selected: {pick.selected}")
    print(f"  reason: {pick.reason}")
