"""Run all connectors and print a status table.

Usage (from project root, venv activated or via .\\.venv\\Scripts\\python.exe):
    python test_connections.py
"""

from __future__ import annotations

import concurrent.futures
import json
import sys
from typing import Iterable

from rich.console import Console
from rich.table import Table

from connectors.base import BaseConnector, ConnectionResult
from connectors.psx_terminal import PSXTerminalConnector
from connectors.yfinance_commodities import YFinanceCommoditiesConnector
from connectors.coingecko import CoinGeckoConnector
from connectors.rss_news import RssNewsConnector
from connectors.psx_portal import (
    PSXCircuitBreakersConnector,
    PSXAnnouncementsConnector,
    PSXIndicesConnector,
    PSXMarketWatchConnector,
)
from connectors.sbp import (
    SBPPolicyRateConnector,
    SBPMarkToMarketConnector,
    SBPEasyDataConnector,
)
from connectors.flows import SCStradeFIPIConnector
from connectors.government import (
    FBRRevenueConnector,
    MoCTradeConnector,
    PBSConnector,
    PBSTradeStatsConnector,
    IMFPakistanConnector,
)

console = Console()


def build_connectors() -> list[BaseConnector]:
    return [
        # Layer 5 — Microstructure / prices
        PSXTerminalConnector(),
        PSXIndicesConnector(),
        PSXMarketWatchConnector(),
        PSXCircuitBreakersConnector(),
        PSXAnnouncementsConnector(),
        # Layer 3 — Flows
        SCStradeFIPIConnector(),
        # Layer 1 — Macro
        SBPPolicyRateConnector(),
        SBPMarkToMarketConnector(),
        SBPEasyDataConnector(),
        YFinanceCommoditiesConnector(),
        # Layer 1 — Fiscal / Trade / Real economy
        FBRRevenueConnector(),
        MoCTradeConnector(),
        PBSTradeStatsConnector(),
        PBSConnector(),
        # Layer 2 — Political / Institutional
        IMFPakistanConnector(),
        # Layer 4 — News / Sentiment
        RssNewsConnector(),
        CoinGeckoConnector(),
    ]


def _run_one(c: BaseConnector) -> ConnectionResult:
    try:
        return c.test()
    except Exception as e:
        return ConnectionResult(
            name=c.name,
            ok=False,
            latency_ms=0.0,
            error=f"Runner crash: {type(e).__name__}: {e}",
        )


def run(connectors: Iterable[BaseConnector]) -> list[ConnectionResult]:
    results: list[ConnectionResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_run_one, c): c for c in connectors}
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
    # Preserve original ordering by name
    order = {c.name: i for i, c in enumerate(connectors)}
    results.sort(key=lambda r: order.get(r.name, 999))
    return results


def _render_table(results: list[ConnectionResult]) -> None:
    table = Table(
        title="PSX Data Source Connection Test",
        title_style="bold cyan",
        show_lines=False,
    )
    table.add_column("Source", style="bold", no_wrap=False)
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Notes / Error", overflow="fold")
    for r in results:
        status = "[green]OK[/green]" if r.ok else "[red]FAIL[/red]"
        latency = f"{r.latency_ms:.0f} ms" if r.latency_ms else "-"
        detail = r.error if r.error else r.notes
        table.add_row(r.name, status, latency, detail or "")
    console.print(table)


def _render_summary(results: list[ConnectionResult]) -> None:
    ok = sum(1 for r in results if r.ok)
    fail = sum(1 for r in results if not r.ok)
    total = len(results)
    console.rule("[bold]Summary[/bold]")
    console.print(
        f"[green]OK: {ok}[/green]   [red]FAIL: {fail}[/red]   [dim]TOTAL: {total}[/dim]"
    )
    if fail:
        console.print("[yellow]Failed sources:[/yellow]")
        for r in results:
            if not r.ok:
                console.print(f"  - [bold]{r.name}[/bold]: {r.error or r.notes}")


def main() -> int:
    console.rule("[bold cyan]PSX Bot — Data Source Connection Test[/bold cyan]")
    connectors = build_connectors()
    console.print(f"Testing [bold]{len(connectors)}[/bold] connectors in parallel...\n")
    results = run(connectors)
    _render_table(results)
    _render_summary(results)

    # Also write a JSON report for programmatic use.
    payload = [
        {
            "name": r.name,
            "ok": r.ok,
            "latency_ms": round(r.latency_ms, 1),
            "sample": r.sample,
            "error": r.error,
            "notes": r.notes,
        }
        for r in results
    ]
    with open("connection_report.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    console.print("\n[dim]Wrote detailed report to[/dim] [cyan]connection_report.json[/cyan]")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
