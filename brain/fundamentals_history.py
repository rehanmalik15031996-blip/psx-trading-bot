"""Point-in-time fundamentals lookup -- the piece that makes a value/quality
strategy backtestable without lookahead bias.

Built on data/fundamentals_history/{SYMBOL}.parquet (see
scripts/build_fundamentals_history.py), which stores one row per fiscal year
with a REAL period-end date from Yahoo Finance's annual financial statements,
not just today's snapshot.

Why a reporting lag matters: a fiscal year ending 2024-06-30 isn't KNOWN to
the market on 2024-06-30 -- PSX-listed companies typically file audited
annual accounts 60-120 days after fiscal year end. Using the raw period_end
date as the "known as of" date would itself be a lookahead bias (using a
number before it was actually public). DEFAULT_REPORT_LAG_DAYS=90 is a
conservative, documented estimate, not a precise per-company filing date --
if this ever needs to be exact, cross-reference data/results/reports.parquet's
`filing_date` field, which has real PSX filing dates for a subset of
(symbol, fy_period) pairs.

Known gaps (documented, not silently hidden):
  - EPS/BVPS use CURRENT shares outstanding applied to historical net
    income/equity (Yahoo doesn't expose historical share counts). PSX share
    counts change rarely outside bonus/rights issues, but this is a real
    approximation.
  - HUBC and ENGROH have no history via this source (empty Yahoo statements
    for HUBC, no Yahoo coverage at all for ENGROH -- see
    connectors/yfinance_fundamentals.py's _NO_YAHOO_COVERAGE).
  - Typically only 3-5 fiscal years available (Yahoo's default annual-
    statement window), not the full 2021-2026 backtest period for every
    symbol -- check coverage before trusting a signal built on this for the
    earliest backtest years.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Union

import pandas as pd

AsOf = Union[str, date, datetime, pd.Timestamp]

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data" / "fundamentals_history"

DEFAULT_REPORT_LAG_DAYS = 90


def _to_ts(as_of: AsOf) -> pd.Timestamp:
    ts = pd.Timestamp(as_of)
    return ts.normalize().tz_localize(None) if ts.tz else ts.normalize()


def load_history(symbol: str) -> pd.DataFrame | None:
    """Raw fiscal-year history for one symbol, sorted by period_end. None if missing."""
    path = HISTORY_DIR / f"{symbol.upper()}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df = df.copy()
    df["period_end"] = pd.to_datetime(df["period_end"])
    return df.sort_values("period_end").reset_index(drop=True)


def point_in_time(
    symbol: str,
    as_of: AsOf,
    report_lag_days: int = DEFAULT_REPORT_LAG_DAYS,
) -> dict | None:
    """Fundamentals actually knowable as of `as_of` -- the most recent fiscal
    year whose (period_end + report_lag_days) <= as_of. Returns None if no
    history exists or no fiscal year had been reported yet by that date
    (correctly conservative: better to return nothing than guess).
    """
    df = load_history(symbol)
    if df is None:
        return None
    as_of_ts = _to_ts(as_of)
    known_by = df["period_end"] + pd.Timedelta(days=report_lag_days)
    eligible = df[known_by <= as_of_ts]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    return {
        "symbol": symbol,
        "as_of": as_of_ts.date().isoformat(),
        "fiscal_year_end": row["period_end"].date().isoformat(),
        "known_from": (row["period_end"] + pd.Timedelta(days=report_lag_days)).date().isoformat(),
        "revenue": row.get("revenue"),
        "net_income": row.get("net_income"),
        "total_equity": row.get("total_equity"),
        "total_assets": row.get("total_assets"),
        "eps": row.get("eps"),
        "bvps": row.get("bvps"),
    }


def point_in_time_pe(symbol: str, as_of: AsOf, price: float,
                      report_lag_days: int = DEFAULT_REPORT_LAG_DAYS) -> float | None:
    """Trailing P/E using only fundamentals knowable as of `as_of`."""
    pit = point_in_time(symbol, as_of, report_lag_days)
    if pit is None or not pit.get("eps") or pit["eps"] <= 0:
        return None
    return round(price / pit["eps"], 2)


def point_in_time_pb(symbol: str, as_of: AsOf, price: float,
                      report_lag_days: int = DEFAULT_REPORT_LAG_DAYS) -> float | None:
    """Price/book using only fundamentals knowable as of `as_of`."""
    pit = point_in_time(symbol, as_of, report_lag_days)
    if pit is None or not pit.get("bvps") or pit["bvps"] <= 0:
        return None
    return round(price / pit["bvps"], 2)


def coverage_report() -> pd.DataFrame:
    """Per-symbol: how many fiscal years of history, earliest/latest period_end.
    Use this before trusting any point-in-time-based signal for a given backtest
    window -- coverage is NOT uniform across the universe or across time."""
    from config.universe import symbols as universe_symbols
    rows = []
    for sym in universe_symbols():
        df = load_history(sym)
        if df is None:
            rows.append({"symbol": sym, "n_years": 0, "earliest": None, "latest": None})
        else:
            rows.append({
                "symbol": sym,
                "n_years": len(df),
                "earliest": df["period_end"].min().date().isoformat(),
                "latest": df["period_end"].max().date().isoformat(),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":  # pragma: no cover  (manual run)
    print(coverage_report().to_string())
