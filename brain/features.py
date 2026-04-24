"""Feature engineering for PSX swing trading.

Inputs:
  - Per-stock OHLCV (close, open, volume) from data/ohlcv/
  - Macro series (brent, gold, copper, usdpkr, btc, cotton, wti) from data/macro/
Output:
  - A long-format DataFrame indexed by (date, symbol) with ~50 features
    and target columns (fwd_ret_5d, fwd_ret_5d_up).

Philosophy: favour simple, robust features that any quant would recognise.
No look-ahead anywhere — every feature uses only data known at close of `date`.
The target is computed from `date+1..date+5` closes and must only be used
for training/evaluation, never as a feature.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

from ta.momentum import RSIIndicator, StochRSIIndicator  # noqa: E402
from ta.trend import MACD  # noqa: E402
from ta.volatility import BollingerBands  # noqa: E402
from ta.volume import OnBalanceVolumeIndicator  # noqa: E402

from config.universe import UNIVERSE, by_sector, sector_of, symbols
from data.store import load_ohlcv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Per-stock feature factory
# --------------------------------------------------------------------------
def _price_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute purely price/volume-derived features for ONE symbol.

    Expects columns: date, symbol, open, close, volume (sorted ascending).
    Returns df with new feature columns added.
    """
    d = df.copy()
    c = d["close"].astype(float)
    v = d["volume"].astype(float).replace(0, np.nan)
    o = d["open"].astype(float)

    # Returns
    d["ret_1d"]  = c.pct_change(1)
    d["ret_5d"]  = c.pct_change(5)
    d["ret_10d"] = c.pct_change(10)
    d["ret_21d"] = c.pct_change(21)
    d["ret_63d"] = c.pct_change(63)

    # Log returns (more symmetric, better for vol)
    log_ret = np.log(c / c.shift(1))
    d["log_ret_1d"] = log_ret

    # Simple moving averages
    for w in (5, 10, 20, 50, 100, 200):
        sma = c.rolling(w).mean()
        d[f"sma_{w}"] = sma
        d[f"px_over_sma_{w}"] = c / sma - 1.0

    # Distance from 20d high / low
    hi20 = c.rolling(20).max()
    lo20 = c.rolling(20).min()
    d["dist_hi20"] = (c - hi20) / hi20
    d["dist_lo20"] = (c - lo20) / lo20.replace(0, np.nan)

    # Realised volatility (close-to-close)
    d["rvol_10d"] = log_ret.rolling(10).std() * np.sqrt(252)
    d["rvol_20d"] = log_ret.rolling(20).std() * np.sqrt(252)
    d["rvol_60d"] = log_ret.rolling(60).std() * np.sqrt(252)
    d["rvol_ratio"] = d["rvol_10d"] / d["rvol_60d"]

    # Technical indicators via the `ta` library
    d["rsi_14"] = RSIIndicator(close=c, window=14).rsi()
    d["rsi_7"]  = RSIIndicator(close=c, window=7).rsi()

    macd = MACD(close=c, window_slow=26, window_fast=12, window_sign=9)
    d["macd"]        = macd.macd()
    d["macd_signal"] = macd.macd_signal()
    d["macd_hist"]   = macd.macd_diff()

    bb = BollingerBands(close=c, window=20, window_dev=2)
    d["bb_width"] = bb.bollinger_wband()
    d["bb_pctb"]  = bb.bollinger_pband()  # 0 = at lower band, 1 = at upper band

    # Stochastic RSI
    try:
        srsi = StochRSIIndicator(close=c, window=14, smooth1=3, smooth2=3)
        d["stoch_rsi"] = srsi.stochrsi()
    except Exception:
        d["stoch_rsi"] = np.nan

    # Close-only stochastic oscillator
    d["stoch_k"] = 100 * (c - lo20) / (hi20 - lo20).replace(0, np.nan)

    # On-Balance Volume (uses volume)
    try:
        d["obv"] = OnBalanceVolumeIndicator(close=c, volume=d["volume"].astype(float)).on_balance_volume()
        d["obv_change_5d"] = d["obv"].pct_change(5)
    except Exception:
        d["obv"] = np.nan
        d["obv_change_5d"] = np.nan

    # Volume features
    d["log_volume"] = np.log(v)
    d["vol_sma_20"] = v.rolling(20).mean()
    d["vol_ratio_20"] = v / d["vol_sma_20"]
    d["turnover_pkr"] = c * v                          # proxy for traded value
    d["turnover_sma_20"] = d["turnover_pkr"].rolling(20).mean()
    d["turnover_ratio"] = d["turnover_pkr"] / d["turnover_sma_20"]

    # Intraday proxy (open-to-close)
    d["oc_ret"] = (c - o) / o

    # Return autocorrelation (mean reversion / momentum signal)
    d["autocorr_5"] = d["ret_1d"].rolling(10).corr(d["ret_1d"].shift(5))

    return d


# --------------------------------------------------------------------------
# Cross-sectional features (universe-wide ranks and sector means)
# --------------------------------------------------------------------------
def _cross_sectional(df: pd.DataFrame) -> pd.DataFrame:
    """Add ranks and sector-relative features. `df` is long-format (date, symbol)."""
    d = df.copy()
    # attach sector
    d["sector"] = d["symbol"].map(lambda s: sector_of(s) or "Other")

    # Cross-sectional ranks (0..1) per day
    for col in ("ret_1d", "ret_5d", "vol_ratio_20", "rsi_14", "rvol_20d"):
        if col in d.columns:
            d[f"{col}_xrank"] = d.groupby("date")[col].rank(pct=True)

    # Sector-relative 5d return
    if "ret_5d" in d.columns:
        sector_mean = d.groupby(["date", "sector"])["ret_5d"].transform("mean")
        d["ret_5d_vs_sector"] = d["ret_5d"] - sector_mean

    return d


# --------------------------------------------------------------------------
# Calendar features
# --------------------------------------------------------------------------
def _calendar(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    dt = pd.to_datetime(d["date"])
    d["dow"]           = dt.dt.dayofweek          # 0=Mon .. 4=Fri
    d["dom"]           = dt.dt.day
    d["month"]         = dt.dt.month
    d["days_to_meom"]  = dt.dt.days_in_month - dt.dt.day
    d["is_month_end"]  = (d["days_to_meom"] <= 2).astype(int)
    return d


# --------------------------------------------------------------------------
# Macro join
# --------------------------------------------------------------------------
def _macro_features(df: pd.DataFrame) -> pd.DataFrame:
    """Left-join macro wide frame and derive first-difference / momentum features."""
    try:
        from scripts.backfill_macro import macro_wide
    except ImportError:
        return df
    wide = macro_wide()
    if wide.empty:
        return df

    m = wide.copy()
    m["date"] = pd.to_datetime(m["date"])
    # Compute macro deltas
    for col in [c for c in m.columns if c != "date"]:
        m[f"{col}_ret_1d"] = m[col].pct_change(1)
        m[f"{col}_ret_5d"] = m[col].pct_change(5)
        m[f"{col}_ret_21d"] = m[col].pct_change(21)
    # Only keep the derived features + level for most series
    keep = ["date"] + [c for c in m.columns if c.endswith(("_ret_1d", "_ret_5d", "_ret_21d"))] + [
        "usdpkr", "brent", "gold", "copper", "btc",
    ]
    keep = [c for c in keep if c in m.columns]
    m = m[keep]

    d = df.copy()
    d["date"] = pd.to_datetime(d["date"]).astype("datetime64[ns]")
    m["date"] = pd.to_datetime(m["date"]).astype("datetime64[ns]")
    merged = pd.merge_asof(
        d.sort_values("date"),
        m.sort_values("date"),
        on="date",
        direction="backward",
    )
    return merged


# --------------------------------------------------------------------------
# Target (forward return) — computed last, must not leak
# --------------------------------------------------------------------------
def _targets(df: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    d = df.copy()
    grp = d.groupby("symbol", group_keys=False)
    d[f"fwd_ret_{horizon}d"] = grp["close"].transform(
        lambda s: s.shift(-horizon) / s - 1.0
    )
    d[f"fwd_ret_{horizon}d_up"] = (d[f"fwd_ret_{horizon}d"] > 0).astype(int)
    return d


# --------------------------------------------------------------------------
# Public builder
# --------------------------------------------------------------------------
def build_features(
    symbols_list: list[str] | None = None,
    horizon: int = 5,
    include_macro: bool = True,
) -> pd.DataFrame:
    """Build the full long-format feature frame for the universe.

    Returns a DataFrame indexed by (date, symbol) with features + target.
    """
    syms = symbols_list or symbols()
    frames = []
    for s in syms:
        raw = load_ohlcv(s)
        if raw.empty:
            continue
        feat = _price_features(raw)
        frames.append(feat)
    if not frames:
        return pd.DataFrame()

    big = pd.concat(frames, ignore_index=True)
    big = _cross_sectional(big)
    big = _calendar(big)
    if include_macro:
        big = _macro_features(big)
    big = _targets(big, horizon=horizon)

    # Drop rows where the target is NaN (last `horizon` bars per symbol) —
    # but only when training. At inference time those are the rows we SCORE.
    return big


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Columns safe to feed into a model (excludes date/symbol/sector/raw prices/target)."""
    drop = {
        "date", "symbol", "sector",
        "open", "close", "volume",
        "fwd_ret_5d", "fwd_ret_5d_up",
        "vol_sma_20", "turnover_sma_20", "turnover_pkr", "obv",
    } | {f"sma_{w}" for w in (5, 10, 20, 50, 100, 200)}
    return [c for c in df.columns if c not in drop and pd.api.types.is_numeric_dtype(df[c])]


if __name__ == "__main__":
    # Quick smoke test
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from rich import print
    df = build_features(include_macro=False)  # skip macro in smoke test
    print(f"[bold]Built:[/bold] {df.shape[0]:,} rows x {df.shape[1]} cols")
    print(f"Symbols: {sorted(df.symbol.unique().tolist())}")
    print(f"Date range: {df.date.min().date()} to {df.date.max().date()}")
    print(f"Feature columns ({len(feature_columns(df))}):")
    print(feature_columns(df))
