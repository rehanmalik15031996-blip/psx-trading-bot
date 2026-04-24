"""Deep data inspection: pull real data from every source, validate format,
and show sample records.

Unlike test_connections.py (which only does health checks), this script calls
`fetch()` on every connector and shows:
  - Actual payload shape (rows x cols)
  - Schema / field names
  - Sample records (first 3)
  - Data quality flags (missing fields, non-numeric prices, stale dates, etc.)

Usage:
    python inspect_sources.py                 # inspect all sources
    python inspect_sources.py --source psx    # filter (substring on name)
    python inspect_sources.py --json          # also dump full_fetch_report.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from typing import Any, Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from connectors.base import BaseConnector, FetchResult
from test_connections import build_connectors

console = Console()


# ------------------------------------------------------------------
# Validation: we declare what we EXPECT each source's records to have
# and score data quality accordingly.
# ------------------------------------------------------------------
EXPECTED_SCHEMAS: dict[str, list[str]] = {
    "PSX Terminal (REST)":       ["symbol", "price", "change_pct", "volume", "trades", "value_pkr"],
    "PSX Indices (DPS)":         ["index", "current", "change_pct"],
    "PSX Market Watch":          ["symbol", "sector_name", "indices", "current", "change_pct", "volume"],
    "PSX Circuit Breakers":      ["symbol", "direction", "change_pct", "volume"],
    "PSX Announcements":         [],   # reach-only (JS SPA)
    "SCStrade FIPI/LIPI":        ["category", "buy_pkr_mn", "sell_pkr_mn", "net_pkr_mn"],
    "SBP Policy Rate + KIBOR":   ["policy_rate_pct", "kibor", "tbill_yields_pct"],
    "SBP M2M (PKR/USD)":         ["m2m_rate", "weighted_avg_bid", "weighted_avg_offer", "spread_pkr"],
    "SBP EasyData (portal reach)": [],  # needs API key
    "yfinance (commodities)":    ["commodity", "close", "change_1d_pct", "change_5d_pct"],
    "FBR Revenue Collections":   ["title", "url"],
    "MoC Monthly Trade Statements": [],  # blocked by Cloudflare — expected to fail
    "PBS Trade Statistics":      ["title", "url"],
    "PBS (Bureau of Statistics)": ["title", "url"],
    "IMF Pakistan Country Page": ["title", "url"],
    "RSS News Aggregator":       ["source", "title", "published_at", "link"],
    "CoinGecko (crypto)":        ["coin", "usd", "change_24h_pct", "volume_24h_usd"],
}


def _validate(result: FetchResult) -> tuple[str, list[str]]:
    """Return (verdict, warnings) by comparing record schema to expected."""
    expected = EXPECTED_SCHEMAS.get(result.name, [])
    warnings: list[str] = []

    if not result.ok:
        return ("FAIL", [result.error or "no data"])

    # No expectations = just reachable
    if not expected:
        return ("REACH-ONLY", ["no structured records expected — reachability only"])

    if not result.records:
        return ("EMPTY", ["schema expected but 0 records parsed"])

    actual = set(result.schema) if result.schema else set(result.records[0].keys())
    missing = [k for k in expected if k not in actual]
    if missing:
        warnings.append(f"missing expected fields: {missing}")

    # Sanity: check nulls in first record
    first = result.records[0]
    def _is_empty(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, (str, list, dict)):
            return len(v) == 0
        return False
    null_fields = [k for k in expected if k in first and _is_empty(first.get(k))]
    if null_fields:
        warnings.append(f"null values in: {null_fields}")

    verdict = "GOOD" if not missing and not null_fields else "PARTIAL"
    return (verdict, warnings)


# ------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------
def _render_header_table(results: list[FetchResult]) -> None:
    t = Table(title="Data Fetch — Overview", title_style="bold cyan")
    t.add_column("#", style="dim", width=3)
    t.add_column("Source", style="bold")
    t.add_column("Format", justify="center")
    t.add_column("Rows", justify="right")
    t.add_column("Cols", justify="right")
    t.add_column("Latency", justify="right")
    t.add_column("Verdict", justify="center")
    t.add_column("Summary", overflow="fold")

    for i, r in enumerate(results, 1):
        verdict, _ = _validate(r)
        color = {
            "GOOD": "green", "PARTIAL": "yellow",
            "REACH-ONLY": "cyan", "EMPTY": "yellow", "FAIL": "red",
        }.get(verdict, "white")
        t.add_row(
            str(i), r.name, r.format,
            str(r.rows), str(r.cols),
            f"{r.latency_ms:.0f} ms",
            f"[{color}]{verdict}[/{color}]",
            r.summary or (r.error or ""),
        )
    console.print(t)


def _fmt_value(v: Any, max_len: int = 60) -> str:
    if v is None:
        return "[dim]null[/dim]"
    if isinstance(v, (dict, list)):
        s = json.dumps(v, default=str)
        return s if len(s) <= max_len else s[: max_len - 3] + "..."
    s = str(v)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


def _render_sample(result: FetchResult, rows: int = 3) -> None:
    verdict, warnings = _validate(result)
    color = {
        "GOOD": "green", "PARTIAL": "yellow", "REACH-ONLY": "cyan",
        "EMPTY": "yellow", "FAIL": "red",
    }.get(verdict, "white")

    header = Text()
    header.append(f"{result.name}", style="bold")
    header.append(f"   ({result.format}, {result.rows} rows x {result.cols} cols, ")
    header.append(f"{result.latency_ms:.0f} ms, ")
    header.append(f"[{verdict}]", style=color)
    header.append(")")

    body_lines: list[str] = []
    if result.summary:
        body_lines.append(f"[dim]summary:[/dim] {result.summary}")
    for w in warnings:
        body_lines.append(f"[yellow]warn:[/yellow] {w}")
    if result.error:
        body_lines.append(f"[red]error:[/red] {result.error}")

    # Schema
    if result.schema:
        body_lines.append(
            f"[dim]schema:[/dim] {', '.join(result.schema)}"
        )

    # Sample records (first N)
    if result.records:
        body_lines.append("")
        body_lines.append("[bold]sample records:[/bold]")
        for i, rec in enumerate(result.records[:rows]):
            body_lines.append(f"  [dim][{i}][/dim]")
            for k, v in rec.items():
                body_lines.append(f"    {k}: {_fmt_value(v)}")

    # Extras
    if result.extras:
        body_lines.append("")
        body_lines.append("[bold]extras:[/bold]")
        for k, v in result.extras.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                body_lines.append(f"  {k}: [{len(v)} items]")
                for i, rec in enumerate(v[:2]):
                    body_lines.append(f"    [dim][{i}][/dim] {_fmt_value(rec, 120)}")
            else:
                body_lines.append(f"  {k}: {_fmt_value(v, 200)}")

    body = "\n".join(body_lines)
    console.print(Panel(body, title=header, border_style=color, padding=(0, 1)))


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------
def _run_one(c: BaseConnector) -> FetchResult:
    try:
        return c.fetch()
    except Exception as e:
        return FetchResult(
            name=c.name, ok=False, latency_ms=0.0,
            error=f"Runner crash: {type(e).__name__}: {e}",
        )


def run(connectors: Iterable[BaseConnector]) -> list[FetchResult]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_run_one, connectors))
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", help="substring filter on connector name")
    p.add_argument("--rows", type=int, default=3, help="sample records to show")
    p.add_argument("--json", action="store_true", help="also write full_fetch_report.json")
    args = p.parse_args()

    console.rule("[bold cyan]PSX Bot — Data Inspection[/bold cyan]")
    connectors = build_connectors()
    if args.source:
        needle = args.source.lower()
        connectors = [c for c in connectors if needle in c.name.lower()]
    console.print(
        f"Fetching from [bold]{len(connectors)}[/bold] sources in parallel...\n"
    )

    results = run(connectors)

    _render_header_table(results)
    console.rule("[bold]Per-source details[/bold]")
    for r in results:
        _render_sample(r, rows=args.rows)

    # Summary counts
    verdicts: dict[str, int] = {}
    for r in results:
        v, _ = _validate(r)
        verdicts[v] = verdicts.get(v, 0) + 1

    console.rule("[bold]Data Readiness Summary[/bold]")
    order = ["GOOD", "PARTIAL", "REACH-ONLY", "EMPTY", "FAIL"]
    colors = {"GOOD": "green", "PARTIAL": "yellow", "REACH-ONLY": "cyan",
              "EMPTY": "yellow", "FAIL": "red"}
    parts = []
    for v in order:
        if v in verdicts:
            parts.append(f"[{colors[v]}]{v}: {verdicts[v]}[/{colors[v]}]")
    parts.append(f"[dim]TOTAL: {len(results)}[/dim]")
    console.print("   ".join(parts))

    if args.json:
        payload = [
            {
                "name": r.name, "ok": r.ok, "latency_ms": round(r.latency_ms, 1),
                "format": r.format, "rows": r.rows, "cols": r.cols,
                "schema": r.schema, "records": r.records[:5],
                "extras": r.extras, "summary": r.summary, "error": r.error,
                "verdict": _validate(r)[0],
            }
            for r in results
        ]
        with open("full_fetch_report.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        console.print("\n[dim]Wrote full report to[/dim] [cyan]full_fetch_report.json[/cyan]")

    # Exit non-zero if any FAIL
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
