"""Volume confirmation signals for the universe.

Validated 2026-05-02 against 5 years of PSX history (scripts/validate_strategy_fixes.py
TEST T4): a +1.5% day on >=1.5x median 20-day volume is followed by +0.80% over
the next 5 trading days on average (n=4,657), versus +0.23% on a +1.5% day with
<=0.7x median volume (n=734). PSX volume DOES confirm direction despite the
market being retail-heavy. We surface this as:

  - per-stock signal in `signals_for(symbol)`
  - universe summary in `universe_summary(symbols, lookback_days=3)`

Both are tolerant of missing OHLCV (returns empty dict / zero counts).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OHLCV_DIR = ROOT / "data" / "ohlcv"

# Validated thresholds from scripts/validate_strategy_fixes.py.
DEFAULT_RETURN_THRESHOLD_PCT = 2.0   # tightened from 1.5% test threshold
DEFAULT_VOLUME_RATIO         = 2.0   # tightened from 1.5x test threshold
DEFAULT_LOOKBACK_DAYS        = 3     # universe count window


def _load_ohlcv(symbol: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{symbol.upper()}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty or "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.set_index("date").sort_index()


def signals_for(symbol: str,
                return_threshold_pct: float = DEFAULT_RETURN_THRESHOLD_PCT,
                volume_ratio: float = DEFAULT_VOLUME_RATIO,
                lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """Per-stock volume signal for the most recent N trading days.

    Returns::

        {
          "symbol": "HUBC",
          "as_of": "2026-04-29",
          "last_close_pct":     -1.7,         # latest day's % return
          "last_vol_ratio_20d": 0.92,         # latest volume / 20d median
          "had_breakout_3d":    True,         # any of the last 3d had a confirmed breakout
          "breakout_dates":     ["2026-04-25"],
          "data_age_days":      0,            # days since last OHLCV row
        }
    """
    df = _load_ohlcv(symbol)
    if df is None:
        return {"symbol": symbol.upper(), "error": "no OHLCV"}

    if "close" not in df.columns or "volume" not in df.columns:
        return {"symbol": symbol.upper(), "error": "missing close/volume cols"}

    df = df[["close", "volume"]].copy()
    df["ret_pct"] = df["close"].pct_change() * 100
    df["vol_ratio_20d"] = df["volume"] / df["volume"].rolling(20, min_periods=10).median()
    df["confirmed_up"] = (
        (df["ret_pct"] >= return_threshold_pct)
        & (df["vol_ratio_20d"] >= volume_ratio)
    ).fillna(False)

    last = df.iloc[-1]
    window = df.tail(lookback_days)
    breakout_days = window[window["confirmed_up"]]
    today = pd.Timestamp.now().normalize().tz_localize(None)
    age = (today - df.index[-1]).days

    return {
        "symbol": symbol.upper(),
        "as_of": df.index[-1].date().isoformat(),
        "last_close_pct": round(float(last["ret_pct"]), 2)
            if pd.notna(last["ret_pct"]) else None,
        "last_vol_ratio_20d": round(float(last["vol_ratio_20d"]), 2)
            if pd.notna(last["vol_ratio_20d"]) else None,
        "had_breakout_3d": bool(not breakout_days.empty),
        "breakout_dates": [d.date().isoformat() for d in breakout_days.index],
        "data_age_days": int(age),
    }


def universe_summary(symbols: list[str],
                     lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                     **kwargs) -> dict:
    """Universe-level volume confirmation rollup.

    Returns::

        {
          "as_of": "2026-04-29",
          "n_universe": 17,
          "n_confirmed_breakouts_3d": 2,
          "breakout_names": ["HUBC", "PSO"],
          "lookback_days": 3,
          "data_freshness_days": 0,
          "per_stock": { "HUBC": {...}, ... }
        }
    """
    per_stock: dict[str, dict] = {}
    breakout_names: list[str] = []
    last_dates: list[pd.Timestamp] = []
    for sym in symbols:
        sig = signals_for(sym, lookback_days=lookback_days, **kwargs)
        per_stock[sym.upper()] = sig
        if sig.get("had_breakout_3d"):
            breakout_names.append(sym.upper())
        if sig.get("as_of"):
            try:
                last_dates.append(pd.Timestamp(sig["as_of"]))
            except Exception:
                pass
    if last_dates:
        most_recent = max(last_dates)
        today = pd.Timestamp.now().normalize().tz_localize(None)
        freshness = int((today - most_recent).days)
        as_of = most_recent.date().isoformat()
    else:
        freshness = None
        as_of = None
    return {
        "as_of": as_of,
        "n_universe": len(symbols),
        "n_confirmed_breakouts_3d": len(breakout_names),
        "breakout_names": breakout_names,
        "lookback_days": lookback_days,
        "data_freshness_days": freshness,
        "per_stock": per_stock,
    }


if __name__ == "__main__":
    # Quick self-check.
    syms = ["HUBC", "OGDC", "MCB", "MEBL", "PSO", "FCCL", "MLCF", "FABL"]
    s = universe_summary(syms)
    import json
    print(json.dumps({k: v for k, v in s.items() if k != "per_stock"},
                     indent=2, default=str))
    print(f"breakout_names: {s['breakout_names']}")
