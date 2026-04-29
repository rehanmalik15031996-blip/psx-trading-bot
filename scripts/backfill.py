"""One-off backfill script: pull ~5 years of daily EOD for the whole universe.

Usage:
    python scripts/backfill.py
    python scripts/backfill.py --symbols OGDC PPL HBL     # subset

Idempotent: re-running overwrites existing Parquet files with the latest
full history from PSX DPS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table

from config.universe import UNIVERSE, symbols as universe_symbols
from connectors.psx_historical import PSXHistoricalConnector
from data.store import save_ohlcv


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill PSX EOD history")
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Optional subset of symbols; default = full universe",
    )
    args = parser.parse_args()

    console = Console()
    symbols = [s.upper() for s in (args.symbols or universe_symbols())]

    console.rule(f"[bold cyan]Backfilling {len(symbols)} symbols from PSX DPS")

    conn = PSXHistoricalConnector()
    probe = conn.test()
    if not probe.ok:
        console.print(f"[red]DPS unreachable:[/red] {probe.error}")
        return 1
    console.print(f"[green]DPS reachable[/green] ({probe.latency_ms:.0f} ms) — {probe.notes}")

    table = Table(title="Backfill results")
    table.add_column("Symbol", style="cyan")
    table.add_column("Sector", style="dim")
    table.add_column("Rows", justify="right")
    table.add_column("First date", style="dim")
    table.add_column("Last date", style="dim")
    table.add_column("Status")

    universe_by_sym = {u.symbol: u for u in UNIVERSE}

    total_ok = 0
    total_rows = 0

    for sym in symbols:
        try:
            rows = conn.fetch_symbol(sym)
        except Exception as e:
            table.add_row(sym, "-", "0", "-", "-", f"[red]ERR {type(e).__name__}[/red]")
            continue

        if not rows:
            table.add_row(sym, "-", "0", "-", "-", "[red]EMPTY[/red]")
            continue

        written = save_ohlcv(sym, rows)
        total_ok += 1
        total_rows += written

        entry = universe_by_sym.get(sym)
        sector = entry.sector if entry else "-"
        dates = sorted(r["date"] for r in rows)
        table.add_row(
            sym, sector, str(written),
            dates[0], dates[-1],
            "[green]OK[/green]",
        )

    console.print(table)
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"{total_ok}/{len(symbols)} symbols written, {total_rows:,} total rows. "
        f"Parquet files in data/ohlcv/"
    )

    try:
        from scripts._health import write_status
        write_status(
            workflow="eod",
            ok=(total_ok >= int(0.8 * len(symbols))),
            note=(f"OHLCV refresh: {total_ok}/{len(symbols)} symbols, "
                  f"{total_rows:,} rows"),
            payload={"symbols_ok":    int(total_ok),
                       "symbols_total": int(len(symbols)),
                       "rows_total":    int(total_rows)},
        )
    except Exception as e:
        console.print(f"[yellow]WARN:[/yellow] _health.write_status "
                       f"failed: {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
