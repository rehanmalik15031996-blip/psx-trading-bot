"""Daily trade-recommendation report generator.

This is the top-level orchestrator that runs end-to-end each trading day:

  1. Pull latest market data from our live connectors (news, ticks, flows).
  2. Rebuild features for the universe.
  3. Score each stock with the trained per-stock ensemble.
  4. Score sentiment per stock from today's news (lexicon).
  5. For each ML-shortlisted stock, call the LLM analyst for a structured verdict.
  6. Apply risk rules (regime detection, sector caps, training-AUC gate).
  7. Mutate the paper portfolio: close stops/exits, open new BUYs.
  8. Write reports/YYYY-MM-DD.md with recommendations + portfolio status.

Usage:
    python scripts/generate_report.py
    python scripts/generate_report.py --dry-run       # don't touch paper portfolio
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from rich.console import Console
from rich.table import Table

from brain.features import build_features, feature_columns
from brain.llm_analyst import analyze_symbol
from brain.models import predict_latest
from brain import paper_portfolio as pp
from brain.risk import (
    RiskConfig,
    check_exit,
    decide_per_stock,
    detect_regime,
    filter_portfolio,
    load_economic_gate,
    load_training_aucs,
)
from brain.sentiment import (
    score_market_sentiment,
    score_news_for_symbol,
)
from config.universe import UNIVERSE, symbols as universe_symbols
from connectors.psx_historical import PSXHistoricalConnector
from connectors.psx_portal import PSXIndicesConnector, PSXMarketWatchConnector
from connectors.rss_news import RssNewsConnector
from data.store import append_ohlcv_row


# --------------------------------------------------------------------------
# Stage 1: refresh data
# --------------------------------------------------------------------------
def refresh_latest_bar(console: Console) -> None:
    """Append today's EOD bar to each symbol's Parquet file (idempotent)."""
    conn = PSXHistoricalConnector()
    probe = conn.test()
    if not probe.ok:
        console.print(f"[yellow]PSX DPS unreachable, using cached history: {probe.error}[/yellow]")
        return
    for sym in universe_symbols():
        try:
            rows = conn.fetch_symbol(sym)
            if rows:
                from data.store import save_ohlcv
                save_ohlcv(sym, rows)
        except Exception as e:
            console.print(f"[yellow]{sym} refresh failed: {e}[/yellow]")


def fetch_live_context(console: Console) -> dict:
    """Pull current macro + news context."""
    context = {
        "news_records": [],
        "kse100_change_pct": None,
        "kse100_level": None,
        "market_sentiment": 0.0,
        "fetch_errors": [],
    }

    # Today's indices (KSE-100 etc.)
    try:
        idx = PSXIndicesConnector().fetch()
        for r in idx.records:
            if r.get("symbol") == "KSE100":
                context["kse100_change_pct"] = r.get("change_pct")
                context["kse100_level"] = r.get("current")
                break
    except Exception as e:
        context["fetch_errors"].append(f"indices: {e}")

    # News feed
    try:
        news = RssNewsConnector().fetch()
        context["news_records"] = news.records or []
    except Exception as e:
        context["fetch_errors"].append(f"news: {e}")

    # Market-wide sentiment
    m = score_market_sentiment(context["news_records"])
    context["market_sentiment"] = m["score"]
    context["n_news_articles"] = m["n_articles"]

    return context


# --------------------------------------------------------------------------
# Stage 2: score each stock
# --------------------------------------------------------------------------
def score_universe(console: Console) -> tuple[pd.DataFrame, list[str]]:
    """Return (latest-features DataFrame, feature_cols)."""
    feat = build_features(include_macro=True)
    cols = feature_columns(feat)
    latest = (
        feat.sort_values(["symbol", "date"])
        .groupby("symbol", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )
    return latest, cols, feat


def _latest_close_and_5d(feat: pd.DataFrame, symbol: str) -> tuple[float | None, float | None]:
    d = feat[feat.symbol == symbol].sort_values("date")
    if d.empty:
        return None, None
    cur = float(d["close"].iloc[-1])
    if len(d) >= 6:
        ret5 = float(d["close"].iloc[-1]) / float(d["close"].iloc[-6]) - 1
    else:
        ret5 = None
    return cur, ret5


# --------------------------------------------------------------------------
# Stage 3+4+5: analyst + risk + portfolio updates
# --------------------------------------------------------------------------
def daily_cycle(dry_run: bool = False, use_llm: bool = True) -> Path:
    console = Console()
    today_iso = date.today().isoformat()
    console.rule(f"[bold cyan]Daily cycle for {today_iso}")

    # --- 1. Refresh data ---
    refresh_latest_bar(console)
    context = fetch_live_context(console)
    console.print(
        f"KSE-100: {context.get('kse100_level')}  "
        f"(chg {context.get('kse100_change_pct')}%)   "
        f"News sentiment: {context.get('market_sentiment'):+.2f}  "
        f"({context.get('n_news_articles', 0)} articles)"
    )

    # --- 2. Score universe ---
    latest, feat_cols, full_feat = score_universe(console)

    # --- 3. Per-stock ML probabilities ---
    aucs = load_training_aucs()
    econ_gate = load_economic_gate()
    if econ_gate:
        viable = [s for s, r in econ_gate.items() if r.get("economically_viable")]
        blocked = [s for s, r in econ_gate.items() if not r.get("economically_viable")]
        console.print(
            f"[dim]Economic gate: {len(viable)} viable, "
            f"{len(blocked)} blocked ({', '.join(blocked) if blocked else 'none'})[/dim]"
        )
    else:
        console.print("[yellow]Economic gate: no data (run `python -m brain.backtest` first)[/yellow]")
    rows_for_analyst: list[dict] = []
    ml_scores: dict[str, float] = {}
    prices: dict[str, float] = {}
    sent_by_symbol: dict[str, dict] = {}

    for u in UNIVERSE:
        p = predict_latest(full_feat, u.symbol, feat_cols)
        ml_scores[u.symbol] = p if p is not None else 0.5
        cur_px, ret5 = _latest_close_and_5d(full_feat, u.symbol)
        if cur_px:
            prices[u.symbol] = cur_px
        s = score_news_for_symbol(context["news_records"], u.symbol)
        sent_by_symbol[u.symbol] = s
        rows_for_analyst.append({
            "symbol": u.symbol,
            "company_name": u.name,
            "sector": u.sector,
            "ml_prob_up": ml_scores[u.symbol],
            "recent_return_5d": ret5,
            "cur_price": cur_px,
            "sentiment": s,
        })

    # --- 4. Regime detection ---
    macro_context = {
        "usdpkr": None, "brent": None, "policy_rate": None,
        "kse100_change_pct": context.get("kse100_change_pct"),
        "fipi_net": None,
    }
    # Latest macro from our backfilled series
    try:
        from scripts.backfill_macro import macro_wide
        mw = macro_wide()
        if not mw.empty:
            last = mw.iloc[-1]
            for k in ("usdpkr", "brent", "gold", "copper", "btc"):
                if k in last.index:
                    macro_context[k] = float(last[k])
    except Exception:
        pass

    # KSE-100 5d change from indices connector isn't historical, so approximate from universe
    # using equal-weight portfolio 5d return as a proxy
    try:
        kse5 = full_feat.groupby("date")["ret_1d"].mean().tail(5).sum()
    except Exception:
        kse5 = None

    regime = detect_regime(
        market_sentiment=context.get("market_sentiment", 0.0),
        kse100_change_5d=kse5,
        fipi_net_5d=None,
    )
    console.print(f"[bold]Regime:[/bold] {regime}")

    # --- 5. LLM analyst (only for ML shortlist) ---
    cfg = RiskConfig()
    def _passes_econ(sym: str) -> bool:
        if not cfg.apply_economic_gate or not econ_gate:
            return True
        row = econ_gate.get(sym)
        if row is None:
            return True  # no data; don't over-filter
        return float(row.get("avg_return", 0.0)) >= cfg.min_wf_avg_return

    shortlist = [
        r for r in rows_for_analyst
        if r["ml_prob_up"] >= cfg.min_ml_prob
        and (aucs.get(r["symbol"]) or 0) >= cfg.min_training_auc
        and _passes_econ(r["symbol"])
    ]
    console.print(f"Shortlisted {len(shortlist)} stocks for LLM analyst")

    decisions = []
    analyst_results = {}
    for r in rows_for_analyst:
        sym = r["symbol"]
        if r in shortlist and use_llm:
            v = analyze_symbol(
                symbol=sym,
                company_name=r["company_name"],
                sector=r["sector"],
                ml_prob_up=r["ml_prob_up"],
                recent_return_5d=r["recent_return_5d"],
                sentiment=r["sentiment"],
                macro_context=macro_context,
                news_snippets=r["sentiment"].get("headlines", []),
            )
        else:
            # Non-shortlist: synthetic HOLD verdict (don't waste LLM calls)
            from brain.llm_analyst import AnalystVerdict
            v = AnalystVerdict(
                symbol=sym, verdict="HOLD", confidence=0.5,
                rationale="Not shortlisted (below ML/AUC threshold).",
                ml_prob_up=r["ml_prob_up"],
                sentiment_score=r["sentiment"]["score"],
            )
        analyst_results[sym] = v

        d = decide_per_stock(
            symbol=sym,
            sector=r["sector"],
            ml_prob=r["ml_prob_up"],
            training_auc=aucs.get(sym),
            llm_verdict=v.verdict,
            llm_confidence=v.confidence,
            sentiment_score=r["sentiment"]["score"],
            regime=regime,
            cfg=cfg,
            economics=econ_gate.get(sym),
        )
        decisions.append(d)

    # --- 6. Portfolio construction (load current state) ---
    state = pp.load()
    existing_by_sector: dict[str, int] = {}
    for sym in state.open_positions:
        from config.universe import sector_of
        sec = sector_of(sym) or "Other"
        existing_by_sector[sec] = existing_by_sector.get(sec, 0) + 1

    decisions = filter_portfolio(
        decisions, cfg=cfg, open_positions_by_sector=existing_by_sector
    )

    # --- 7. Check exits on existing positions ---
    exits = []
    for sym, pos in list(state.open_positions.items()):
        cur_px = prices.get(sym, pos.entry_px)
        v = analyst_results.get(sym)
        llm_verd = v.verdict if v else "HOLD"
        ml_prob = ml_scores.get(sym, 0.5)
        ex = check_exit(
            symbol=sym,
            entry_px=pos.entry_px,
            peak_px=pos.peak_px,
            current_px=cur_px,
            hold_days=pos.hold_days,
            ml_prob=ml_prob,
            llm_verdict=llm_verd,
            cfg=cfg,
        )
        exits.append(ex)

    # --- 8. Mutate paper portfolio ---
    actions_taken: list[dict] = []
    if not dry_run:
        # 8a. Close any exits
        for ex in exits:
            if ex.should_exit:
                cur_px = prices.get(ex.symbol)
                if cur_px:
                    trade = pp.close_position(state, ex.symbol, cur_px, ex.reason)
                    if trade:
                        actions_taken.append({
                            "type": "SELL",
                            "symbol": ex.symbol,
                            "price": cur_px,
                            "shares": trade.shares,
                            "pnl_pkr": trade.pnl_pkr,
                            "return_pct": trade.return_pct,
                            "reason": ex.reason,
                        })

        # 8b. Open new BUYs (after exits freed up cash)
        for d in decisions:
            if d.action == "BUY" and d.symbol not in state.open_positions:
                cur_px = prices.get(d.symbol)
                if cur_px is None:
                    continue
                pos = pp.open_position(
                    state=state,
                    symbol=d.symbol,
                    target_pct=d.size_pct,
                    price=cur_px,
                    entry_prob=d.ml_prob,
                    reason=d.reason,
                )
                if pos:
                    actions_taken.append({
                        "type": "BUY",
                        "symbol": d.symbol,
                        "price": cur_px,
                        "shares": pos.shares,
                        "size_pkr": pos.cost,
                        "reason": d.reason,
                    })

        # 8c. Mark-to-market & persist
        pp.mark_to_market(state, prices)
        pp.save(state)

    # --- 9. Write report ---
    report_path = _write_md_report(
        today_iso=today_iso,
        context=context,
        regime=regime,
        rows=rows_for_analyst,
        ml_scores=ml_scores,
        aucs=aucs,
        decisions=decisions,
        analyst_results=analyst_results,
        exits=exits,
        actions_taken=actions_taken,
        state=state,
        prices=prices,
        dry_run=dry_run,
    )

    # --- 10. Console summary ---
    _print_console_summary(console, decisions, exits, actions_taken, state, prices)
    console.print(f"\n[green]Report written:[/green] {report_path}")
    return report_path


# --------------------------------------------------------------------------
# Report / console helpers
# --------------------------------------------------------------------------
def _badge(action: str) -> str:
    return {
        "BUY":  "[bold green]BUY[/bold green]",
        "HOLD": "[yellow]HOLD[/yellow]",
        "SELL": "[bold red]SELL[/bold red]",
        "SKIP": "[dim]SKIP[/dim]",
    }.get(action, action)


def _print_console_summary(console, decisions, exits, actions, state, prices):
    table = Table(title="Today's decisions")
    for col in ("Symbol", "Action", "MLp", "LLM", "Conf", "Sent", "Size%", "Reason"):
        table.add_column(col)
    for d in decisions:
        table.add_row(
            d.symbol, _badge(d.action),
            f"{d.ml_prob:.2f}", d.llm_verdict,
            f"{d.llm_confidence:.2f}", f"{d.sentiment:+.2f}",
            f"{d.size_pct:.0%}" if d.size_pct else "-",
            d.reason[:60],
        )
    console.print(table)

    if actions:
        a_table = Table(title="Paper-trading actions executed")
        for col in ("Type", "Symbol", "Price", "Shares", "Size / PnL", "Reason"):
            a_table.add_column(col)
        for a in actions:
            if a["type"] == "BUY":
                pnl = f"{a['size_pkr']:,.0f} PKR"
            else:
                pnl = f"{a['pnl_pkr']:+,.0f} PKR ({a['return_pct']:+.2%})"
            a_table.add_row(
                a["type"], a["symbol"], f"{a['price']:.2f}",
                str(a["shares"]), pnl, a["reason"][:50],
            )
        console.print(a_table)

    s = pp.summary(state, prices)
    console.print(
        f"\n[bold]Portfolio:[/bold] equity {s['total_equity']:,.0f} PKR  "
        f"(cash {s['cash']:,.0f}, open {s['open_positions']})  "
        f"total return {s['total_return_pct']:+.2%}"
    )


def _write_md_report(today_iso, context, regime, rows, ml_scores, aucs,
                     decisions, analyst_results, exits, actions_taken,
                     state, prices, dry_run) -> Path:
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{today_iso}.md"

    s = pp.summary(state, prices)

    lines = [
        f"# PSX Trading Bot — Daily Report {today_iso}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        + ("   (DRY RUN — portfolio not touched)" if dry_run else ""),
        "",
        "## Market context",
        f"- KSE-100 level: {context.get('kse100_level')}    change today: {context.get('kse100_change_pct')}%",
        f"- News articles scanned: {context.get('n_news_articles', 0)}",
        f"- Market-wide sentiment: {context.get('market_sentiment', 0):+.2f}",
        f"- **Regime: {regime}**",
        "",
        "## Decisions",
        "",
        "| Symbol | Sector | Action | ML prob | AUC | LLM verdict | Conf | Sentiment | Size | Reason |",
        "|---|---|---|---:|---:|---|---:|---:|---:|---|",
    ]
    from config.universe import sector_of
    for d in decisions:
        auc = aucs.get(d.symbol)
        auc_s = f"{auc:.2f}" if auc is not None else "-"
        size_s = f"{d.size_pct:.0%}" if d.size_pct else "-"
        lines.append(
            f"| {d.symbol} | {sector_of(d.symbol) or '-'} | **{d.action}** | "
            f"{d.ml_prob:.2f} | {auc_s} | {d.llm_verdict} | "
            f"{d.llm_confidence:.2f} | {d.sentiment:+.2f} | {size_s} | {d.reason} |"
        )

    # Analyst notes for BUYs
    lines.extend(["", "## Analyst notes (for BUY candidates)", ""])
    buys = [d for d in decisions if d.action == "BUY"]
    if buys:
        for d in buys:
            v = analyst_results.get(d.symbol)
            if not v:
                continue
            lines.append(f"### {d.symbol}")
            lines.append(f"- Rationale: {v.rationale}")
            if v.key_risks:
                lines.append(f"- Key risks: {'; '.join(v.key_risks)}")
            lines.append("")
    else:
        lines.append("(no BUY signals today)")

    # Exits
    lines.extend(["", "## Existing positions — exit check", ""])
    if exits:
        lines.append("| Symbol | Exit? | Reason |")
        lines.append("|---|---|---|")
        for ex in exits:
            lines.append(f"| {ex.symbol} | {'YES' if ex.should_exit else 'no'} | {ex.reason} |")
    else:
        lines.append("(no open positions)")

    # Actions taken
    if actions_taken:
        lines.extend(["", "## Actions executed in paper portfolio", ""])
        lines.append("| Type | Symbol | Price | Shares | Size / PnL | Reason |")
        lines.append("|---|---|---:|---:|---:|---|")
        for a in actions_taken:
            if a["type"] == "BUY":
                lines.append(
                    f"| BUY | {a['symbol']} | {a['price']:.2f} | {a['shares']} | "
                    f"{a['size_pkr']:,.0f} PKR | {a['reason'][:80]} |"
                )
            else:
                lines.append(
                    f"| SELL | {a['symbol']} | {a['price']:.2f} | {a['shares']} | "
                    f"{a['pnl_pkr']:+,.0f} PKR ({a['return_pct']:+.2%}) | {a['reason'][:80]} |"
                )

    # Portfolio snapshot
    lines.extend(["", "## Portfolio snapshot", ""])
    lines.append(f"- Cash: {s['cash']:,.0f} PKR")
    lines.append(f"- Open positions: {s['open_positions']}  (value {s['open_positions_value']:,.0f} PKR)")
    lines.append(f"- **Total equity: {s['total_equity']:,.0f} PKR**")
    lines.append(f"- Return since inception: **{s['total_return_pct']:+.2%}**")
    lines.append(f"- Closed trades: {s['n_closed_trades']}    Win rate: {s['win_rate']:.1%}")

    if state.open_positions:
        lines.extend(["", "### Open positions detail", "",
                      "| Symbol | Shares | Entry px | Current px | Unrealized PnL | Hold days |",
                      "|---|---:|---:|---:|---:|---:|"])
        for sym, pos in state.open_positions.items():
            cur = prices.get(sym, pos.entry_px)
            unrealized = (cur - pos.entry_px) * pos.shares
            ret = cur / pos.entry_px - 1
            lines.append(
                f"| {sym} | {pos.shares} | {pos.entry_px:.2f} | {cur:.2f} | "
                f"{unrealized:+,.0f} ({ret:+.2%}) | {pos.hold_days} |"
            )

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate report but don't touch paper portfolio")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip LLM analyst (use rule-based fallback for all)")
    args = parser.parse_args()

    try:
        daily_cycle(dry_run=args.dry_run, use_llm=not args.no_llm)
        return 0
    except Exception as e:
        print(f"Daily cycle failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
