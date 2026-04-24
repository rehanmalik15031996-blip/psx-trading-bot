"""Macro time-series backfill using yfinance.

We need HISTORICAL series (not just today's values from our live connectors)
so features can condition on multi-year context. yfinance handles:
  - Brent, WTI, Gold, Copper (commodities)
  - BTC-USD (risk-on proxy)
  - USDPKR=X (PKR exchange rate)

Stored as data/macro/{key}.parquet, indexed by date, single 'value' column.
Policy rate / KIBOR histories are not freely downloadable at daily frequency,
so we synthesize a step-function from known SBP decisions (maintained manually
in a small CSV if/when we need it). For now macro = FX + commodities + BTC.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table

MACRO_DIR = PROJECT_ROOT / "data" / "macro"
MACRO_DIR.mkdir(parents=True, exist_ok=True)

SERIES: dict[str, str] = {
    "brent":   "BZ=F",
    "wti":     "CL=F",
    "gold":    "GC=F",
    "copper":  "HG=F",
    "cotton":  "CT=F",
    "btc":     "BTC-USD",
    "usdpkr":  "PKR=X",
}


def backfill(start: str = "2020-01-01") -> pd.DataFrame:
    console = Console()
    table = Table(title="Macro backfill")
    table.add_column("Series", style="cyan")
    table.add_column("Ticker", style="dim")
    table.add_column("Rows", justify="right")
    table.add_column("Range", style="dim")

    for key, ticker in SERIES.items():
        try:
            df = yf.download(ticker, start=start, progress=False, auto_adjust=False)
            if df is None or df.empty:
                table.add_row(key, ticker, "0", "[red]EMPTY[/red]")
                continue
            s = df["Close"].copy()
            if hasattr(s, "columns"):
                s = s.iloc[:, 0]
            out = pd.DataFrame({"date": s.index, "value": s.values}).dropna()
            out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None)
            out = out.sort_values("date").reset_index(drop=True)
            out.to_parquet(MACRO_DIR / f"{key}.parquet", engine="pyarrow", index=False)
            table.add_row(
                key, ticker, str(len(out)),
                f"{out.date.min().date()} to {out.date.max().date()}",
            )
        except Exception as e:
            table.add_row(key, ticker, "0", f"[red]{type(e).__name__}[/red]")

    console.print(table)
    return pd.DataFrame()


def load_macro(key: str) -> pd.DataFrame:
    p = MACRO_DIR / f"{key}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p, engine="pyarrow")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def macro_wide() -> pd.DataFrame:
    """Return wide-format macro frame: one row per date, one column per series."""
    frames = []
    for key in SERIES:
        df = load_macro(key)
        if df.empty:
            continue
        frames.append(df.rename(columns={"value": key}).set_index("date"))
    if not frames:
        return pd.DataFrame()
    wide = pd.concat(frames, axis=1).sort_index()
    wide = wide.ffill().dropna(how="all")
    return wide.reset_index()


if __name__ == "__main__":
    backfill()
