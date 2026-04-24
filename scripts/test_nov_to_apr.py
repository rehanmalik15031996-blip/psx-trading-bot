"""Walk-forward test: November 2025 → April 2026.

At each month-end rebalance the Phase 1 rule sees ONLY data up to that date
and commits to a set of picks for the next month. This script replays those
decisions month-by-month and compares them against what actually happened.

How we guarantee no look-ahead:
  * `pick_monthly(wide, as_of, cfg)` reads only data at index <= as_of
    (momentum/vol are rolling windows whose values at T use only [T-w, T]).
  * `backtest_v2.simulate(...)` loops day-by-day; each rebalance triggers a
    fresh `pick_monthly` call with `as_of = that day`.
  * Therefore the picks committed at 2025-10-31 cannot "know" anything about
    November 2025, etc.

The script prints:
  1. Per-month: rebalance date, picks, strategy return, buy-and-hold return,
     per-pick outcome, best names we missed (if we picked something) or the
     best/worst the universe did (if we were in cash).
  2. Overall: total return, CAGR (annualised), Sharpe, max DD vs buy-and-hold.
  3. Trade log for the window.

It also writes a markdown summary to `reports/walkforward_nov_to_apr.md`.
"""

from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from brain.backtest_v2 import simulate
from brain.strategy import StrategyConfig, build_prices_wide
from config.universe import symbols as universe_symbols


START = pd.Timestamp("2025-11-01")
END = pd.Timestamp("2026-04-30")


def fmt_pct(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "   n/a"
    return f"{float(x) * 100:+.{digits}f}%"


def main(use_overlay: bool = False):
    print("=" * 78)
    print(f"PLAN D WALK-FORWARD TEST — {START.date()} to {END.date()}")
    print(f"Regime overlay: {'ON (rule-based)' if use_overlay else 'OFF'}")
    print("=" * 78)

    wide = build_prices_wide(universe_symbols())
    print(f"\nData: {wide.shape[0]} daily rows × {wide.shape[1]} symbols "
          f"({wide.index[0].date()} → {wide.index[-1].date()})")

    cfg = StrategyConfig()
    print(f"Config: top_n={cfg.top_n}  mom_window={cfg.momentum_window}d  "
          f"vol_cap={cfg.vol_rank_cap:.0%}  cost={cfg.cost_round_trip:.2%}/rt  "
          f"market_filter={cfg.market_filter_on}  "
          f"trailing_stop={cfg.use_trailing_stop}")

    print("\nRunning walk-forward simulation… ", end="", flush=True)
    result = simulate(wide, cfg=cfg, use_regime_overlay=use_overlay,
                      include_cost_sensitivity=False)
    print(f"done ({len(result.picks_log)} rebalances total).")

    # Slice to the test window
    eq = result.equity_curve
    bench = result.benchmark_curve
    rets = result.daily_returns
    bench_rets = bench.pct_change().fillna(0.0)
    mask = (eq.index >= START) & (eq.index <= END)
    if not mask.any():
        print(f"\nERROR: no trading days in {START.date()}..{END.date()}.")
        return

    eq_slice = eq[mask]
    bench_slice = bench[mask]
    rets_slice = rets[mask]
    bench_rets_slice = bench_rets[mask]

    eq_rebased = eq_slice / eq_slice.iloc[0]
    bench_rebased = bench_slice / bench_slice.iloc[0]

    # ------------------------------------------------------------------
    # Month-by-month narrative
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("MONTH-BY-MONTH WALK-FORWARD")
    print("=" * 78)

    test_months = pd.date_range(START, END, freq="ME")  # month-ends in window
    monthly_rows = []

    for month_end in test_months:
        month_label = month_end.strftime("%Y-%m")
        m_start = pd.Timestamp(month_end.year, month_end.month, 1)

        # The rebalance that DECIDED this month's positions is the latest
        # picks_log entry strictly before the month start.
        decisive = None
        for p in result.picks_log:
            pdt = pd.Timestamp(p["date"])
            if pdt < m_start:
                decisive = p
            else:
                break
        if decisive is None:
            continue

        picks = decisive["selected"]
        market_on = decisive["market_risk_on"]
        reason = decisive["reason"]

        # Strategy & benchmark returns compounded over the month
        month_mask = (rets_slice.index >= m_start) & (rets_slice.index <= month_end)
        strat_m = float((1 + rets_slice[month_mask]).prod() - 1) \
            if month_mask.any() else np.nan
        bench_m = float((1 + bench_rets_slice[month_mask]).prod() - 1) \
            if month_mask.any() else np.nan

        # Per-symbol monthly change in the universe (for hindsight)
        month_wide = wide[(wide.index >= m_start) & (wide.index <= month_end)]
        per_name = (month_wide.iloc[-1] / month_wide.iloc[0] - 1).dropna() \
            if not month_wide.empty else pd.Series(dtype=float)
        per_name = per_name.sort_values(ascending=False)

        print(f"\n[{month_label}]  rebalance on {decisive['date']}  "
              f"market_on={market_on}")
        print(f"  Picks           : "
              f"{', '.join(picks) if picks else '(none — CASH)'}")
        if reason:
            print(f"  Rule reason     : {reason}")
        print(f"  Strategy return : {fmt_pct(strat_m)}")
        print(f"  Buy&Hold ret.   : {fmt_pct(bench_m)}")
        print(f"  Edge            : {fmt_pct(strat_m - bench_m)}")

        if picks:
            print(f"  Per-pick outcome during {month_label}:")
            for sym in picks:
                if sym in per_name.index:
                    print(f"    {sym:<6s}  {fmt_pct(per_name[sym])}")
            missed = per_name.drop(picks, errors="ignore")
            if not missed.empty:
                print("  Best names we missed:")
                for sym, ret in missed.head(3).items():
                    print(f"    {sym:<6s}  {fmt_pct(ret)}")
        else:
            if not per_name.empty:
                top3 = per_name.head(3)
                bot3 = per_name.tail(3)
                print(f"  We held CASH. Universe this month:")
                print(f"    Best:  {', '.join(f'{s} {fmt_pct(r)}' for s, r in top3.items())}")
                print(f"    Worst: {', '.join(f'{s} {fmt_pct(r)}' for s, r in bot3.items())}")

        monthly_rows.append({
            "month": month_label,
            "rebalance": decisive["date"],
            "market_on": market_on,
            "picks": ", ".join(picks) if picks else "CASH",
            "strategy_%": strat_m * 100,
            "buy_hold_%": bench_m * 100,
            "edge_%": (strat_m - bench_m) * 100,
        })

    # ------------------------------------------------------------------
    # Overall summary
    # ------------------------------------------------------------------
    strat_total = float(eq_rebased.iloc[-1] - 1)
    bench_total = float(bench_rebased.iloc[-1] - 1)
    n_days = int(len(rets_slice))
    years = n_days / 252
    strat_cagr = (1 + strat_total) ** (1 / years) - 1 if years > 0 else 0
    bench_cagr = (1 + bench_total) ** (1 / years) - 1 if years > 0 else 0
    strat_vol = float(rets_slice.std() * np.sqrt(252))
    strat_sharpe = (float(rets_slice.mean() / rets_slice.std() * np.sqrt(252))
                    if rets_slice.std() > 0 else 0.0)
    bench_vol = float(bench_rets_slice.std() * np.sqrt(252))
    bench_sharpe = (float(bench_rets_slice.mean() / bench_rets_slice.std() * np.sqrt(252))
                    if bench_rets_slice.std() > 0 else 0.0)
    strat_dd = float((eq_rebased / eq_rebased.cummax() - 1).min())
    bench_dd = float((bench_rebased / bench_rebased.cummax() - 1).min())

    print("\n" + "=" * 78)
    print("OVERALL SUMMARY")
    print("=" * 78)
    print(f"Trading days            : {n_days}")
    print(f"Strategy total return   : {fmt_pct(strat_total)}")
    print(f"Buy&Hold total return   : {fmt_pct(bench_total)}")
    print(f"Edge (absolute)         : {fmt_pct(strat_total - bench_total)}")
    print(f"Strategy CAGR (ann.)    : {fmt_pct(strat_cagr)}")
    print(f"Buy&Hold CAGR (ann.)    : {fmt_pct(bench_cagr)}")
    print(f"Strategy volatility     : {fmt_pct(strat_vol)}")
    print(f"Buy&Hold volatility     : {fmt_pct(bench_vol)}")
    print(f"Strategy Sharpe (ann.)  : {strat_sharpe:+.2f}")
    print(f"Buy&Hold Sharpe (ann.)  : {bench_sharpe:+.2f}")
    print(f"Strategy max drawdown   : {fmt_pct(strat_dd)}")
    print(f"Buy&Hold max drawdown   : {fmt_pct(bench_dd)}")

    # Monthly table
    if monthly_rows:
        df = pd.DataFrame(monthly_rows)
        print("\nMonthly breakdown:")
        for col in ("strategy_%", "buy_hold_%", "edge_%"):
            df[col] = df[col].map(lambda v: f"{v:+.2f}")
        print(df.to_string(index=False))

    # ------------------------------------------------------------------
    # Trade log
    # ------------------------------------------------------------------
    trades = [t for t in result.trades
              if pd.Timestamp(t.entry_date) >= START - pd.Timedelta(days=5)
              and pd.Timestamp(t.exit_date) <= END + pd.Timedelta(days=5)]

    print("\n" + "=" * 78)
    print(f"TRADES IN THE WINDOW ({len(trades)} total)")
    print("=" * 78)

    if not trades:
        print("(no trades — strategy was in cash for the full window)")
    else:
        wins = [t for t in trades if t.ret_pct > 0]
        losses = [t for t in trades if t.ret_pct <= 0]
        print(f"Wins: {len(wins)}  Losses: {len(losses)}  "
              f"Win rate: {len(wins) / len(trades) * 100:.1f}%")
        if wins:
            print(f"Avg win:  {np.mean([t.ret_pct for t in wins]) * 100:+.2f}%")
        if losses:
            print(f"Avg loss: {np.mean([t.ret_pct for t in losses]) * 100:+.2f}%")
        print()
        print(f"{'Symbol':<7s}{'Entry':<13s}{'Exit':<13s}{'Hold':<6s}"
              f"{'EntryPx':<10s}{'ExitPx':<10s}{'Return':<10s}Reason")
        for t in sorted(trades, key=lambda x: x.entry_date):
            print(f"{t.symbol:<7s}{t.entry_date:<13s}{t.exit_date:<13s}"
                  f"{t.hold_days:<6d}{t.entry_px:<10.2f}{t.exit_px:<10.2f}"
                  f"{t.ret_pct * 100:+7.2f}%   {t.exit_reason}")

    # ------------------------------------------------------------------
    # Write a clean markdown report
    # ------------------------------------------------------------------
    report_path = PROJECT_ROOT / "reports" / "walkforward_nov_to_apr.md"
    _write_markdown(report_path, monthly_rows, trades, strat_total, bench_total,
                    strat_cagr, bench_cagr, strat_sharpe, bench_sharpe,
                    strat_dd, bench_dd, eq_rebased, bench_rebased,
                    use_overlay=use_overlay)
    print(f"\nReport written: {report_path}")


def _write_markdown(path, monthly, trades, strat_total, bench_total,
                    strat_cagr, bench_cagr, strat_sharpe, bench_sharpe,
                    strat_dd, bench_dd, eq, bench, use_overlay=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append(f"# Walk-forward test — Nov 2025 → Apr 2026")
    lines.append("")
    lines.append(f"Plan D Phase 1, regime overlay: "
                 f"**{'ON (rule-based)' if use_overlay else 'OFF'}**. "
                 f"At every month-end the rule sees only data up to that date; "
                 f"no future leakage.\n")

    lines.append("## Headline numbers\n")
    lines.append("| Metric | Strategy | Buy & Hold |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Total return (6 months) | {strat_total * 100:+.2f}% | "
                 f"{bench_total * 100:+.2f}% |")
    lines.append(f"| Annualised CAGR | {strat_cagr * 100:+.2f}% | "
                 f"{bench_cagr * 100:+.2f}% |")
    lines.append(f"| Sharpe (annualised) | {strat_sharpe:+.2f} | "
                 f"{bench_sharpe:+.2f} |")
    lines.append(f"| Max drawdown | {strat_dd * 100:+.2f}% | "
                 f"{bench_dd * 100:+.2f}% |")
    lines.append("")

    lines.append("## Month-by-month\n")
    lines.append("| Month | Rebalance | Market on | Picks | Strategy | Buy&Hold | Edge |")
    lines.append("|---|---|:-:|---|---:|---:|---:|")
    for r in monthly:
        lines.append(
            f"| {r['month']} | {r['rebalance']} | "
            f"{'yes' if r['market_on'] else 'no'} | "
            f"{r['picks']} | {r['strategy_%']} | {r['buy_hold_%']} | "
            f"{r['edge_%']} |"
        )
    lines.append("")

    if trades:
        lines.append(f"## Trade log ({len(trades)} trades)\n")
        lines.append("| Symbol | Entry | Exit | Hold | Entry px | Exit px | Return | Reason |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|")
        for t in sorted(trades, key=lambda x: x.entry_date):
            lines.append(
                f"| {t.symbol} | {t.entry_date} | {t.exit_date} | "
                f"{t.hold_days} | {t.entry_px:.2f} | {t.exit_px:.2f} | "
                f"{t.ret_pct * 100:+.2f}% | {t.exit_reason} |"
            )
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--with-overlay", action="store_true",
                   help="Apply rule-based regime overlay (exposure scaling).")
    args = p.parse_args()
    main(use_overlay=args.with_overlay)
