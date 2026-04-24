"""Day-by-day walk-forward test for April 2026.

Runs two parallel checks:

1. The "official" prediction: at 2026-03-31 (month-end) the Phase 1 rule produces
   its April picks. Those picks are held for the whole month. We walk forward one
   day at a time through April, using ONLY data available as of each day, and
   report the portfolio's equity vs equal-weight buy-and-hold.

2. The "daily stability" check: for each April day, pretend we were rebalancing
   TODAY (using only data up to today). What would the top-5 picks be? This
   tells us whether the March 31 selection was stable or whether the ranking
   moved around a lot during the month.

No look-ahead is used: all signals at date t are computed from prices at
dates <= t. The backtest equity curve is marked to market with today's close.

Outputs:
  - reports/april_walkforward.md  (day-by-day table + picks)
  - reports/april_walkforward_per_stock.csv
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from brain.strategy import (
    StrategyConfig, build_prices_wide,
    compute_momentum, compute_realized_vol,
    pick_monthly,
)
from config.universe import symbols as universe_symbols


REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)


def freeze_at(wide: pd.DataFrame, freeze_date: pd.Timestamp) -> pd.DataFrame:
    """Return a slice of `wide` with dates <= freeze_date (simulates being at t)."""
    return wide[wide.index <= freeze_date]


def run() -> int:
    console = Console()
    cfg = StrategyConfig()

    console.rule("[bold cyan]April 2026 walk-forward test")

    wide = build_prices_wide(universe_symbols())
    console.print(f"Full price frame: {wide.shape[0]} days × {wide.shape[1]} symbols")
    console.print(f"Date range: {wide.index.min().date()} → {wide.index.max().date()}\n")

    # ----------------------------------------------------------------
    # 1. Freeze data at end of March 2026 and take the rebalance pick
    # ----------------------------------------------------------------
    march_end = pd.Timestamp("2026-03-31")
    wide_at_march = freeze_at(wide, march_end)

    mar_pick = pick_monthly(wide_at_march, march_end, cfg)

    console.rule("[bold]1. Official April picks (using data through 2026-03-31)")
    console.print(f"As-of date: {march_end.date()}")
    console.print(f"Market risk-on: {mar_pick.market_risk_on}")
    console.print(f"Reason: {mar_pick.reason}\n")

    rank_t = Table(title="Universe ranking at 2026-03-31 (150d momentum)")
    rank_t.add_column("Rank", justify="right")
    rank_t.add_column("Symbol")
    rank_t.add_column("150d log-ret", justify="right")
    rank_t.add_column("20d rvol", justify="right")
    rank_t.add_column("Pick?", justify="center")

    vol_mar = compute_realized_vol(wide_at_march, cfg.vol_window).loc[march_end]
    for i, (sym, score) in enumerate(mar_pick.ranked_all.items(), 1):
        rv = vol_mar.get(sym, np.nan)
        is_pick = "YES" if sym in mar_pick.selected else ""
        rank_t.add_row(str(i), sym, f"{score:+.2%}",
                       f"{rv:.2%}" if pd.notna(rv) else "—", is_pick)
    console.print(rank_t)

    selected = mar_pick.selected
    console.print(f"\n[bold]Selected for April:[/bold] {selected or 'CASH (market filter veto)'}\n")

    # Also compute the "would-be" top-5 ignoring the market filter, to isolate
    # the momentum-ranking question from the market-gate question.
    no_gate_cfg = StrategyConfig(market_filter_on=False, top_n=cfg.top_n)
    nogate_pick = pick_monthly(wide_at_march, march_end, no_gate_cfg)
    would_be = nogate_pick.selected
    console.print(f"[dim]If market filter were OFF:[/dim] would pick {would_be}\n")

    # ----------------------------------------------------------------
    # 2. Walk forward day-by-day through April
    # ----------------------------------------------------------------
    april_days = wide.index[(wide.index >= pd.Timestamp("2026-04-01"))
                            & (wide.index <= pd.Timestamp("2026-04-30"))]
    console.rule(f"[bold]2. Walk-forward through April ({len(april_days)} trading days)")

    # Build equity curves from the March 31 pick held throughout April.
    if selected:
        picks_prices = wide[selected]
    else:
        picks_prices = pd.DataFrame(index=wide.index)

    # Strategy: held `selected` names equal-weighted, 1/N each. If CASH, equity flat.
    # Enter at 2026-03-31 close at the selected names. Entry cost = half of 40 bps.
    n_names = max(len(selected), 1)
    entry_cost = 0.002    # 20 bps one-way
    # We'll track equity under equal-weight compounding.
    # Assume we bought at March 31 close; first April return is realized the next day.

    entry_prices = wide.loc[march_end, selected] if selected else pd.Series(dtype=float)

    # Buy & hold on the full universe (equal-weight since 2026-03-31)
    bh_entry = wide.loc[march_end]

    rows: list[dict] = []
    per_stock_log: list[dict] = []
    strategy_equity = 1.0 - entry_cost
    bh_equity = 1.0 - entry_cost   # same entry cost assumption

    daily_strat_rets: list[float] = []
    daily_bh_rets: list[float] = []

    prev_date = march_end
    for dt in april_days:
        # Strategy daily return: mean pct-change of the selected names
        if selected:
            strat_r = ((wide.loc[dt, selected] / wide.loc[prev_date, selected]) - 1).mean()
        else:
            strat_r = 0.0     # in cash

        # B&H universe daily return (equal-weighted, all 15 names)
        valid_cols = [c for c in wide.columns
                      if pd.notna(wide.loc[prev_date, c]) and pd.notna(wide.loc[dt, c])]
        bh_r = ((wide.loc[dt, valid_cols] / wide.loc[prev_date, valid_cols]) - 1).mean()

        strategy_equity *= (1 + strat_r)
        bh_equity *= (1 + bh_r)
        daily_strat_rets.append(float(strat_r))
        daily_bh_rets.append(float(bh_r))

        # "If we rebalanced today" stability check
        wide_today = freeze_at(wide, dt)
        today_pick = pick_monthly(wide_today, dt, cfg)
        today_top = today_pick.selected
        overlap = len(set(today_top) & set(selected)) if selected and today_top else 0
        overlap_pct = overlap / max(cfg.top_n, 1) * 100

        rows.append({
            "date": dt.date().isoformat(),
            "strat_daily_ret": strat_r,
            "bh_daily_ret": bh_r,
            "strat_equity": strategy_equity,
            "bh_equity": bh_equity,
            "alpha_cum": strategy_equity - bh_equity,
            "mkt_on_today": today_pick.market_risk_on,
            "today_top": ", ".join(today_top) if today_top else "CASH",
            "overlap_with_march_pick": overlap_pct,
        })

        # Per-stock intraday: for each selected name, daily return and cumulative
        if selected:
            for sym in selected:
                px_t = wide.loc[dt, sym]
                px_prev = wide.loc[prev_date, sym]
                px_entry = entry_prices[sym]
                per_stock_log.append({
                    "date": dt.date().isoformat(),
                    "symbol": sym,
                    "daily_ret": float(px_t / px_prev - 1),
                    "cum_ret_from_entry": float(px_t / px_entry - 1),
                })

        prev_date = dt

    # ----------------------------------------------------------------
    # 3. Print the day-by-day table
    # ----------------------------------------------------------------
    day_t = Table(title="Day-by-day walk-forward")
    day_t.add_column("Date")
    day_t.add_column("Strat ret", justify="right")
    day_t.add_column("B&H ret", justify="right")
    day_t.add_column("Strat eq", justify="right")
    day_t.add_column("B&H eq", justify="right")
    day_t.add_column("Alpha cum", justify="right")
    day_t.add_column("Today's top-5", justify="left")
    day_t.add_column("Overlap w/ Mar", justify="right")
    for r in rows:
        day_t.add_row(
            r["date"],
            f"{r['strat_daily_ret']:+.2%}",
            f"{r['bh_daily_ret']:+.2%}",
            f"{r['strat_equity']:.4f}",
            f"{r['bh_equity']:.4f}",
            f"{(r['alpha_cum']):+.4f}",
            r["today_top"] if r["today_top"] != ", ".join(selected) else "(same)",
            f"{r['overlap_with_march_pick']:.0f}%",
        )
    console.print(day_t)

    # ----------------------------------------------------------------
    # 4. April summary
    # ----------------------------------------------------------------
    strat_ret_month = strategy_equity - 1
    bh_ret_month = bh_equity - 1
    alpha = strat_ret_month - bh_ret_month
    strat_daily = np.array(daily_strat_rets)
    bh_daily = np.array(daily_bh_rets)

    def sh(x):
        return float(np.mean(x) / np.std(x) * np.sqrt(252)) if np.std(x) > 0 else 0.0

    console.rule("[bold]3. April summary")
    summary = Table()
    summary.add_column("Metric")
    summary.add_column("Strategy", justify="right")
    summary.add_column("B&H", justify="right")
    summary.add_column("Δ", justify="right")
    summary.add_row("April return", f"{strat_ret_month:+.2%}", f"{bh_ret_month:+.2%}",
                    f"{alpha*100:+.2f} pp")
    summary.add_row("April daily Sharpe (ann.)",
                    f"{sh(strat_daily):+.2f}", f"{sh(bh_daily):+.2f}",
                    f"{sh(strat_daily) - sh(bh_daily):+.2f}")
    summary.add_row("Best day", f"{strat_daily.max():+.2%}", f"{bh_daily.max():+.2%}", "")
    summary.add_row("Worst day", f"{strat_daily.min():+.2%}", f"{bh_daily.min():+.2%}", "")
    summary.add_row("Days up", f"{int((strat_daily>0).sum())}/{len(strat_daily)}",
                    f"{int((bh_daily>0).sum())}/{len(bh_daily)}", "")
    console.print(summary)

    # ----------------------------------------------------------------
    # 5. Predictive-power review (always run; uses `would_be` if cash)
    # ----------------------------------------------------------------
    picks_for_review = selected if selected else would_be
    review_label = ("Selected for April" if selected
                    else "Would-have-picked (market filter OFF)")
    if picks_for_review:
        console.rule(f"[bold]4. Per-stock April performance ({review_label})")
        ps_t = Table()
        ps_t.add_column("Symbol")
        ps_t.add_column("150d mom @ Mar31", justify="right")
        ps_t.add_column("Entry px (Mar31)", justify="right")
        ps_t.add_column("Exit px (latest)", justify="right")
        ps_t.add_column("April ret", justify="right")
        ps_t.add_column("April ret vs universe", justify="right")
        last_day = april_days[-1]
        bh_apr_ret = bh_ret_month
        for sym in picks_for_review:
            mom_score = mar_pick.ranked_all[sym]
            ent = wide.loc[march_end, sym]
            exi = wide.loc[last_day, sym]
            s_ret = exi / ent - 1
            ps_t.add_row(sym, f"{mom_score:+.2%}", f"{ent:.2f}", f"{exi:.2f}",
                         f"{s_ret:+.2%}", f"{(s_ret - bh_apr_ret)*100:+.2f} pp")
        console.print(ps_t)

        # Full universe ranked by actual April return — did the rule pick winners?
        console.rule("[bold]5. Actual April return for ALL 15 stocks (ranked)")
        actual = {}
        for sym in wide.columns:
            try:
                r_ = wide.loc[last_day, sym] / wide.loc[march_end, sym] - 1
                if pd.notna(r_):
                    actual[sym] = r_
            except KeyError:
                pass
        actual_ser = pd.Series(actual).sort_values(ascending=False)
        rank_t2 = Table()
        rank_t2.add_column("Rank", justify="right")
        rank_t2.add_column("Symbol")
        rank_t2.add_column("Apr return", justify="right")
        rank_t2.add_column("150d mom @ Mar31", justify="right")
        rank_t2.add_column("Was picked?", justify="center")
        for i, (sym, r_) in enumerate(actual_ser.items(), 1):
            mom_score = mar_pick.ranked_all.get(sym, np.nan)
            if sym in picks_for_review:
                pick_mark = "YES" if selected else "(if gate off)"
            else:
                pick_mark = ""
            rank_t2.add_row(str(i), sym, f"{r_:+.2%}",
                            f"{mom_score:+.2%}" if pd.notna(mom_score) else "—",
                            pick_mark)
        console.print(rank_t2)

        # How well did 150d momentum rank predict April return? Spearman rank IC.
        both = pd.DataFrame({
            "mom": mar_pick.ranked_all,
            "actual": actual_ser,
        }).dropna()
        rank_ic = both["mom"].corr(both["actual"], method="spearman")
        pearson_ic = both["mom"].corr(both["actual"], method="pearson")
        console.print(f"\n[bold]Predictive-power check[/bold] (momentum ranking at "
                      f"Mar 31 vs actual April return across 15 stocks):")
        console.print(f"  Spearman rank IC = {rank_ic:+.3f}")
        console.print(f"  Pearson IC      = {pearson_ic:+.3f}")
        console.print(f"  (IC > 0 means higher 150d momentum correlated with better "
                      f"April performance)")

    # ----------------------------------------------------------------
    # 6. Write artifacts
    # ----------------------------------------------------------------
    out_md = REPORT_DIR / "april_walkforward.md"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("# April 2026 walk-forward test\n\n")
        f.write(f"Data frozen at 2026-03-31 for the monthly pick. Each subsequent "
                f"April day uses only data available up to that day.\n\n")
        f.write(f"## March 31 monthly pick\n\n")
        f.write(f"- Market risk-on: **{mar_pick.market_risk_on}**\n")
        f.write(f"- Reason: {mar_pick.reason}\n")
        f.write(f"- Selected: `{', '.join(selected) if selected else 'CASH'}`\n\n")
        f.write(f"## April summary\n\n")
        f.write(f"| Metric | Strategy | B&H | Δ |\n|---|---:|---:|---:|\n")
        f.write(f"| April return | {strat_ret_month:+.2%} | {bh_ret_month:+.2%} | "
                f"{alpha*100:+.2f} pp |\n")
        f.write(f"| April daily Sharpe | {sh(strat_daily):+.2f} | {sh(bh_daily):+.2f} |"
                f" {sh(strat_daily)-sh(bh_daily):+.2f} |\n")
        f.write(f"| Days up | {int((strat_daily>0).sum())}/{len(strat_daily)} | "
                f"{int((bh_daily>0).sum())}/{len(bh_daily)} | |\n\n")
        f.write("## Day-by-day\n\n")
        f.write("| Date | Strat ret | B&H ret | Strat eq | B&H eq | Alpha cum | "
                "Top-5 if rebal today | Overlap |\n")
        f.write("|---|---:|---:|---:|---:|---:|---|---:|\n")
        for r in rows:
            top_display = r["today_top"] if r["today_top"] != ", ".join(selected) else "(same)"
            f.write(f"| {r['date']} | {r['strat_daily_ret']:+.2%} | "
                    f"{r['bh_daily_ret']:+.2%} | {r['strat_equity']:.4f} | "
                    f"{r['bh_equity']:.4f} | {r['alpha_cum']:+.4f} | {top_display} | "
                    f"{r['overlap_with_march_pick']:.0f}% |\n")
    console.print(f"\n[dim]Report written to {out_md}[/dim]")

    out_csv = REPORT_DIR / "april_walkforward_per_stock.csv"
    pd.DataFrame(per_stock_log).to_csv(out_csv, index=False)
    console.print(f"[dim]Per-stock log written to {out_csv}[/dim]")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
