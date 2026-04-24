"""Plan D daily runner.

This is dramatically simpler than the old generate_report.py because the
strategy is mechanical:

Daily flow:
  1. Refresh PSX EOD bars (so today's close is in the parquet files)
  2. Load paper portfolio + current prices
  3. Mark-to-market open positions
  4. If today is a month-end rebalance: compute target picks, open/close to
     match, log action. Optionally ask LLM overlay for regime multiplier.
  5. If NOT a rebalance day: show status + "next rebalance date" + optional
     news scan on held positions (emergency-exit hook).
  6. Write reports/YYYY-MM-DD.md with today's state.

Usage:
    python scripts/generate_report_v2.py                # standard daily run
    python scripts/generate_report_v2.py --dry-run      # don't mutate portfolio
    python scripts/generate_report_v2.py --force-rebal  # treat today as rebal
    python scripts/generate_report_v2.py --no-llm       # skip LLM overlay
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from brain.strategy import (
    StrategyConfig,
    build_prices_wide,
    compute_momentum,
    compute_realized_vol,
    pick_monthly,
)
from brain import paper_portfolio as pp
from brain import overlay as ov
from config.universe import UNIVERSE, symbols as universe_symbols, sector_of

REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# Step 1: refresh data (best-effort; falls back to cached if offline)
# ----------------------------------------------------------------------
def refresh_data(console: Console) -> None:
    try:
        from connectors.psx_historical import PSXHistoricalConnector
        from data.store import save_ohlcv
    except Exception as e:
        console.print(f"[yellow]Connectors unavailable: {e}[/yellow]")
        return
    conn = PSXHistoricalConnector()
    probe = conn.test()
    if not probe.ok:
        console.print(f"[yellow]PSX DPS offline, using cached data: {probe.error}[/yellow]")
        return
    for sym in universe_symbols():
        try:
            rows = conn.fetch_symbol(sym)
            if rows:
                save_ohlcv(sym, rows)
        except Exception as e:
            console.print(f"[yellow]{sym} refresh failed: {e}[/yellow]")


# ----------------------------------------------------------------------
# Step 2: detect rebalance
# ----------------------------------------------------------------------
def is_rebalance_day(prices_wide: pd.DataFrame, cfg: StrategyConfig,
                     today: pd.Timestamp) -> tuple[bool, pd.Timestamp | None]:
    """Return (is_rebal, next_rebal_date).

    Rebalance days are the last trading day of each month, derived from the
    actual price calendar (not calendar month-end).
    """
    rebal_dates = (prices_wide.index
                   .to_series()
                   .resample(cfg.rebalance_freq)
                   .last())
    is_rebal = today in set(rebal_dates.values)
    future = [d for d in rebal_dates.values if pd.Timestamp(d) > today]
    next_rebal = pd.Timestamp(future[0]) if future else None
    return is_rebal, next_rebal


# ----------------------------------------------------------------------
# Step 3: build today's snapshot for overlay context
# ----------------------------------------------------------------------
def build_universe_snapshot(prices_wide: pd.DataFrame,
                            today: pd.Timestamp) -> dict:
    if today not in prices_wide.index:
        valid = prices_wide.index[prices_wide.index <= today]
        if len(valid) == 0:
            return {}
        today = valid[-1]
    lr = np.log(prices_wide).diff()
    mom5 = lr.rolling(5).sum().loc[today].mean()
    mom21 = lr.rolling(21).sum().loc[today].mean()
    # Breadth: % of stocks up on today
    day_ret = prices_wide.pct_change().loc[today]
    breadth = (day_ret > 0).mean()
    return {
        "universe_ret_5d": float(mom5) if pd.notna(mom5) else None,
        "universe_ret_21d": float(mom21) if pd.notna(mom21) else None,
        "breadth_pct_up": round(float(breadth), 3) if pd.notna(breadth) else None,
        "kse100_change_5d": None,  # TODO: add when KSE-100 connector works
    }


def build_macro_context() -> dict:
    """Best-effort macro context from cached macro data."""
    try:
        from scripts.backfill_macro import macro_wide
    except ImportError:
        return {}
    try:
        m = macro_wide()
        if m.empty:
            return {}
        latest = m.iloc[-1]
        prev5 = m.iloc[-6] if len(m) >= 6 else latest
        ctx = {}
        for col in ("usdpkr", "brent", "gold", "policy_rate"):
            if col in latest:
                ctx[col] = float(latest[col]) if pd.notna(latest[col]) else None
        if "usdpkr" in latest and "usdpkr" in prev5:
            a, b = float(latest["usdpkr"]), float(prev5["usdpkr"])
            ctx["usdpkr_change_5d"] = round((a / b - 1), 4) if b else None
        return ctx
    except Exception:
        return {}


# ----------------------------------------------------------------------
# Step 4: rebalance execution
# ----------------------------------------------------------------------
def execute_rebalance(
    state: pp.PortfolioState,
    prices_wide: pd.DataFrame,
    today: pd.Timestamp,
    cfg: StrategyConfig,
    regime_decision: ov.RegimeDecision,
    dry_run: bool,
    console: Console,
) -> list[dict]:
    """Align paper portfolio to the target set produced by pick_monthly.

    Returns a list of action records for logging.
    """
    pick = pick_monthly(prices_wide, today, cfg)
    actions = []

    console.rule(f"[bold cyan]Monthly rebalance — {today.date()}")
    console.print(f"Pick reason: [white]{pick.reason}[/white]")
    if pick.selected:
        console.print(f"Selected: [bold]{pick.selected}[/bold]")
    else:
        console.print("[yellow]→ GO TO CASH[/yellow]")
    console.print(f"Regime: [bold]{regime_decision.regime}[/bold] "
                  f"(×{regime_decision.multiplier:.2f}): {regime_decision.reason}")
    per_weight = regime_decision.multiplier / cfg.top_n

    cur_positions = set(state.open_positions.keys())
    target_positions = set(pick.selected)
    today_prices = {sym: float(prices_wide.loc[today, sym])
                    for sym in prices_wide.columns
                    if pd.notna(prices_wide.loc[today, sym])}

    # 1. Close positions not in new target set
    for sym in cur_positions - target_positions:
        px = today_prices.get(sym)
        if px is None:
            continue
        if not dry_run:
            closed = pp.close_position(state, sym, price=px,
                                       reason="rebalance: rotated out")
            actions.append({"type": "CLOSE", "symbol": sym,
                            "price": px,
                            "pnl": closed.pnl_pkr if closed else None})
        else:
            actions.append({"type": "CLOSE_DRY", "symbol": sym, "price": px})

    # 2. Open new positions for symbols entering target
    for sym in target_positions - cur_positions:
        px = today_prices.get(sym)
        if px is None:
            continue
        if not dry_run:
            pos = pp.open_position(state, sym, target_pct=per_weight,
                                   price=px, entry_prob=0.0,
                                   reason=f"rebal: top-{cfg.top_n} {cfg.momentum_window}d mom")
            actions.append({"type": "OPEN", "symbol": sym, "price": px,
                            "shares": pos.shares if pos else 0})
        else:
            actions.append({"type": "OPEN_DRY", "symbol": sym,
                            "price": px, "target_pct": per_weight})

    # 3. Rebalance existing positions to new weight (optional — skip if small diff)
    # For simplicity: leave existing in place; only rotate out/in on set changes.

    return actions


# ----------------------------------------------------------------------
# Step 5: daily maintenance (non-rebal days)
# ----------------------------------------------------------------------
def daily_maintenance(
    state: pp.PortfolioState,
    prices_wide: pd.DataFrame,
    today: pd.Timestamp,
    cfg: StrategyConfig,
    dry_run: bool,
    console: Console,
) -> list[dict]:
    """On non-rebal days: mark-to-market, check trailing stops (if enabled),
    check news-based emergency exits (if LLM available and news connector works).
    """
    actions = []
    today_prices = {sym: float(prices_wide.loc[today, sym])
                    for sym in prices_wide.columns
                    if pd.notna(prices_wide.loc[today, sym])}

    # Mark-to-market (updates peak_px, hold_days)
    if not dry_run:
        snap = pp.mark_to_market(state, today_prices)
        console.print(f"Equity: PKR {snap['equity']:,.0f} "
                      f"(cash {snap['cash']:,.0f}, "
                      f"positions {snap['positions_value']:,.0f})")

    # Trailing stops (off by default in production config)
    if cfg.use_trailing_stop:
        for sym, pos in list(state.open_positions.items()):
            cur = today_prices.get(sym)
            if cur is None:
                continue
            if cur <= pos.peak_px * (1 - cfg.trailing_stop_pct):
                if not dry_run:
                    closed = pp.close_position(
                        state, sym, price=cur,
                        reason=f"trailing stop -{int(cfg.trailing_stop_pct*100)}%")
                    actions.append({"type": "STOP", "symbol": sym, "price": cur,
                                    "pnl": closed.pnl_pkr if closed else None})

    return actions


# ----------------------------------------------------------------------
# Step 6: write report
# ----------------------------------------------------------------------
def write_report(
    today: pd.Timestamp,
    state: pp.PortfolioState,
    prices_wide: pd.DataFrame,
    cfg: StrategyConfig,
    rebal_info: tuple[bool, pd.Timestamp | None],
    universe_snap: dict,
    macro_ctx: dict,
    regime: ov.RegimeDecision | None,
    actions: list[dict],
    pick_reason: str,
    selected: list[str],
) -> Path:
    is_rebal, next_rebal = rebal_info
    today_prices = {sym: float(prices_wide.loc[today, sym])
                    for sym in prices_wide.columns
                    if pd.notna(prices_wide.loc[today, sym])}
    summary = pp.summary(state, today_prices)

    # Benchmark for reference
    lr = np.log(prices_wide).diff()
    uni_cum = (1 + prices_wide.pct_change().mean(axis=1)).cumprod()
    if len(state.equity_history) >= 2:
        eq_start = state.equity_history[0]["equity"]
        eq_now = state.equity_history[-1]["equity"]
        first_date = pd.Timestamp(state.equity_history[0]["date"])
        bench_start = uni_cum.reindex([first_date]).iloc[0] if first_date in uni_cum.index else 1.0
        bench_now = uni_cum.iloc[-1]
        bench_ret = bench_now / bench_start - 1
        strat_ret = eq_now / eq_start - 1
    else:
        bench_ret = None
        strat_ret = 0.0

    lines = [
        f"# PSX Daily Report — {today.date()}",
        "",
        f"_Generated {datetime.now():%Y-%m-%d %H:%M:%S}_",
        "",
        "## Portfolio snapshot",
        "",
        f"- Equity: PKR {summary['total_equity']:,.0f} "
        f"(return {summary['total_return_pct']:+.2%})",
        f"- Cash: PKR {summary['cash']:,.0f}",
        f"- Open positions: {summary['open_positions']} "
        f"(value PKR {summary['open_positions_value']:,.0f})",
        f"- Closed trades: {summary['n_closed_trades']} "
        f"(win rate {summary['win_rate']:.0%})",
    ]
    if bench_ret is not None:
        lines.append(f"- vs. B&H benchmark: strategy {strat_ret:+.2%} vs. "
                     f"B&H {bench_ret:+.2%} (alpha {(strat_ret-bench_ret):+.2%})")

    lines.append("")
    lines.append("## Open positions")
    if state.open_positions:
        lines.append("| Symbol | Entry date | Entry px | Current | Peak | P&L % | Hold days |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for sym, pos in state.open_positions.items():
            cur = today_prices.get(sym, pos.entry_px)
            pnl_pct = cur / pos.entry_px - 1
            lines.append(f"| {sym} | {pos.entry_date} | {pos.entry_px:.2f} | "
                         f"{cur:.2f} | {pos.peak_px:.2f} | "
                         f"{pnl_pct:+.2%} | {pos.hold_days} |")
    else:
        lines.append("_no open positions (in cash)_")

    lines.append("")
    lines.append("## Universe snapshot (today)")
    lines.append(f"- 5d universe return: {_pct(universe_snap.get('universe_ret_5d'))}")
    lines.append(f"- 21d universe return: {_pct(universe_snap.get('universe_ret_21d'))}")
    lines.append(f"- Breadth (% up today): {universe_snap.get('breadth_pct_up', 'n/a')}")
    if macro_ctx:
        lines.append("")
        lines.append("## Macro context")
        for k, v in macro_ctx.items():
            lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append("## Today's picks (if rebalance)")
    if is_rebal:
        lines.append(f"**Rebalance day.** Reason: {pick_reason}")
        lines.append(f"Selected: {selected or 'CASH'}")
        if regime is not None:
            lines.append(f"Regime overlay: **{regime.regime}** "
                         f"(×{regime.multiplier:.2f}) — {regime.reason}")
    else:
        lines.append(f"Not a rebalance day. Next rebalance: "
                     f"{next_rebal.date() if next_rebal is not None else 'n/a'}")

    lines.append("")
    lines.append("## Actions taken today")
    if actions:
        for a in actions:
            lines.append(f"- {a}")
    else:
        lines.append("_no actions_")

    path = REPORT_DIR / f"{today.date()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _pct(v) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):+.2%}"
    except (TypeError, ValueError):
        return str(v)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't mutate paper portfolio")
    parser.add_argument("--force-rebal", action="store_true",
                        help="Treat today as a rebalance day")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM overlay (use rule-based fallback)")
    parser.add_argument("--skip-refresh", action="store_true",
                        help="Don't call PSX DPS; use cached data")
    args = parser.parse_args()

    console = Console()
    console.rule(f"[bold green]PSX Daily Report — {datetime.now():%Y-%m-%d}")

    cfg = StrategyConfig()
    if args.no_llm:
        os.environ["ANTHROPIC_API_KEY"] = ""  # force fallback

    # 1. Refresh data
    if not args.skip_refresh:
        refresh_data(console)

    # 2. Build wide frame
    wide = build_prices_wide(universe_symbols())
    if wide.empty:
        console.print("[red]No price data available — aborting[/red]")
        return 1
    today = wide.index[-1]
    console.print(f"Data through: [bold]{today.date()}[/bold]")

    # 3. Load portfolio
    state = pp.load()

    # 4. Rebalance detection
    is_rebal, next_rebal = is_rebalance_day(wide, cfg, today)
    if args.force_rebal:
        is_rebal = True
        console.print("[yellow]--force-rebal: treating today as a rebalance day[/yellow]")

    # 5. Context for overlay
    universe_snap = build_universe_snapshot(wide, today)
    macro_ctx = build_macro_context()

    regime = None
    actions = []
    pick_reason = ""
    selected = []

    if is_rebal:
        regime = ov.regime_multiplier(macro_ctx, universe_snap, market_news=[])
        actions = execute_rebalance(state, wide, today, cfg, regime,
                                    args.dry_run, console)
        pick = pick_monthly(wide, today, cfg)
        pick_reason = pick.reason
        selected = pick.selected
    else:
        actions = daily_maintenance(state, wide, today, cfg, args.dry_run, console)
        console.print(f"[dim]Not a rebalance day. Next rebalance: "
                      f"{next_rebal.date() if next_rebal is not None else 'n/a'}[/dim]")

    # 6. Save portfolio and write report
    if not args.dry_run:
        pp.save(state)
    else:
        console.print("[yellow]--dry-run: portfolio NOT saved[/yellow]")

    report_path = write_report(
        today=today, state=state, prices_wide=wide, cfg=cfg,
        rebal_info=(is_rebal, next_rebal),
        universe_snap=universe_snap, macro_ctx=macro_ctx,
        regime=regime, actions=actions,
        pick_reason=pick_reason, selected=selected,
    )
    console.print(f"[green]Report written: {report_path}[/green]")

    # Summary table
    today_prices = {sym: float(wide.loc[today, sym])
                    for sym in wide.columns
                    if pd.notna(wide.loc[today, sym])}
    summary = pp.summary(state, today_prices)
    t = Table(title="Portfolio summary")
    t.add_column("Metric"); t.add_column("Value")
    t.add_row("Equity (PKR)", f"{summary['total_equity']:,.0f}")
    t.add_row("Total return", f"{summary['total_return_pct']:+.2%}")
    t.add_row("Cash", f"{summary['cash']:,.0f}")
    t.add_row("Open positions", str(summary["open_positions"]))
    t.add_row("Closed trades", f"{summary['n_closed_trades']} "
                               f"(WR {summary['win_rate']:.0%})")
    console.print(t)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
