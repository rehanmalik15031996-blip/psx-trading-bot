"""Build/refresh point-in-time fundamentals history for the universe.

Fixes the gap flagged in docs/strategy_evaluation_2026-09-01.md: fundamentals
were a single current snapshot with no history, making any value/quality
strategy impossible to backtest without lookahead bias.

Yahoo Finance's default annual-statement window already returns REAL
fiscal-year-end dates alongside each figure (see
connectors/yfinance_fundamentals.py::fetch_history_one) -- the existing
refresh_fundamentals.py just discarded those dates and kept only values.
This script keeps them, appending each fiscal year found to a per-symbol
parquet, deduped by (symbol, period_end) so re-runs are idempotent and the
history accumulates a new row automatically each time a company reports a
new fiscal year.

Storage: data/fundamentals_history/{SYMBOL}.parquet
Consumed by: brain/fundamentals_history.py (point-in-time lookup with a
             reporting-lag assumption -- read that module before using this
             for anything backtest-facing).

Usage:
    python scripts/build_fundamentals_history.py
    python scripts/build_fundamentals_history.py --symbols OGDC PPL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import pandas as pd
from rich.console import Console
from rich.table import Table

from connectors.yfinance_fundamentals import YFinanceFundamentalsConnector
from config.universe import symbols as universe_symbols

OUT_DIR = PROJECT_ROOT / "data" / "fundamentals_history"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def build_one(conn: YFinanceFundamentalsConnector, symbol: str) -> int:
    """Fetch + upsert history for one symbol. Returns rows added (0 if none/failed)."""
    rows = conn.fetch_history_one(symbol)
    if not rows:
        return 0
    new_df = pd.DataFrame(rows)
    new_df.insert(0, "symbol", symbol)

    path = OUT_DIR / f"{symbol}.parquet"
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        before = len(combined)
        combined = combined.drop_duplicates(subset=["symbol", "period_end"], keep="last")
        combined = combined.sort_values("period_end")
        added = len(combined) - len(existing)
        combined.to_parquet(path, index=False)
        return max(added, 0)
    else:
        new_df = new_df.sort_values("period_end")
        new_df.to_parquet(path, index=False)
        return len(new_df)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="subset of symbols (default: full universe)")
    args = parser.parse_args()

    console = Console()
    syms = args.symbols or universe_symbols()
    conn = YFinanceFundamentalsConnector()

    table = Table(title="Fundamentals history build")
    table.add_column("Symbol")
    table.add_column("Fiscal years found")
    table.add_column("New rows")

    total_new = 0
    n_ok = 0
    for sym in syms:
        try:
            path = OUT_DIR / f"{sym}.parquet"
            before_n = len(pd.read_parquet(path)) if path.exists() else 0
            added = build_one(conn, sym)
            after_n = len(pd.read_parquet(path)) if path.exists() else 0
            table.add_row(sym, str(after_n), f"+{added}" if added else "0")
            total_new += added
            if after_n > 0:
                n_ok += 1
        except Exception as e:
            table.add_row(sym, "ERROR", f"{type(e).__name__}: {e}"[:40])

    console.print(table)
    console.print(f"\n{n_ok}/{len(syms)} symbols have history, "
                  f"{total_new} new fiscal-year rows added this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
