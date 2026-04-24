"""Daily pipeline orchestrator (Plan D).

Steps:
  1. Refresh macro series (USD/PKR, commodities) via yfinance.
  2. Refresh OHLCV for all universe stocks from PSX DPS.
  3. Run the v2 daily report (strategy, portfolio, overlay).

Scheduled for ~6:00 PM PKT every trading day (after PSX close at 3:30 PM and
after DPS publishes EOD data, typically by 5 PM).

Note: there is NO model-training step anymore. Plan D is rule-based. See
`psx_strategy_v2.md` for the design.

Usage:
    python scripts/daily_pipeline.py
    python scripts/daily_pipeline.py --skip-macro    # skip yfinance refresh
    python scripts/daily_pipeline.py --no-llm        # offline overlay
    python scripts/daily_pipeline.py --dry-run       # don't mutate portfolio
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console

PY = sys.executable


def _step(console: Console, title: str) -> None:
    console.rule(f"[bold cyan]{title}")


def _run(cmd: list[str], console: Console) -> int:
    console.print(f"[dim]> {' '.join(cmd)}[/dim]")
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-macro", action="store_true")
    parser.add_argument("--skip-ohlcv", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    console = Console()
    console.rule(f"[bold green]Daily pipeline — {datetime.now():%Y-%m-%d %H:%M:%S}")

    if not args.skip_macro:
        _step(console, "1/3  Refresh macro time-series (yfinance)")
        rc = _run([PY, "scripts/backfill_macro.py"], console)
        if rc != 0:
            console.print("[yellow]macro refresh failed, continuing with cached data[/yellow]")

    if not args.skip_ohlcv:
        _step(console, "2/3  Refresh PSX EOD bars")
        rc = _run([PY, "scripts/backfill.py"], console)
        if rc != 0:
            console.print("[yellow]OHLCV refresh failed, continuing with cached data[/yellow]")

    _step(console, "3/3  Generate daily trade report (Plan D)")
    cmd = [PY, "scripts/generate_report_v2.py"]
    if args.no_llm:
        cmd.append("--no-llm")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.skip_ohlcv or args.skip_macro:
        cmd.append("--skip-refresh")
    rc = _run(cmd, console)

    console.rule(f"[bold green]Pipeline finished (exit {rc})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
