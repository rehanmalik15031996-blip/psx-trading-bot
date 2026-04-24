"""Plan D: honest end-to-end backtest.

Simulates the Phase 1 strategy:
  - Monthly rotation into top-N by 150d momentum, vol-filtered, market-gated
  - Per-position trailing stop
  - Optional rule-based regime overlay (for comparison; LLM version not
    deterministic so not used in backtest)

Reports a complete metrics bundle:
  - Absolute: CAGR, volatility, max drawdown
  - Risk-adjusted: Sharpe, Sortino, Calmar
  - Stability: rolling 1Y Sharpe (mean, min)
  - Benchmark comparison vs equal-weight buy-and-hold
  - Per-regime breakdown (calendar-year)
  - Trade-level summary (n_trades, win rate, avg return per trade)
  - Cost sensitivity (same strategy at 20/40/60/100 bps round-trip)
  - Monkey-test reference (our rule beats what % of random monthly top-3?)
"""

from __future__ import annotations

import json
import os
import sys
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from rich import print
from rich.table import Table
from rich.console import Console

from brain.strategy import (
    StrategyConfig, pick_monthly, build_prices_wide,
    compute_momentum, compute_realized_vol, trailing_stop_hit,
)
from config.universe import symbols as universe_symbols

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------
@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_px: float
    exit_px: float
    peak_px: float
    hold_days: int
    ret_pct: float
    exit_reason: str


@dataclass
class BacktestResult:
    config: dict
    period: tuple[str, str]
    daily_returns: pd.Series
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    trades: list[Trade]
    picks_log: list[dict]
    metrics: dict
    per_year: pd.DataFrame
    cost_sensitivity: pd.DataFrame


def _next_trading_day(idx: pd.DatetimeIndex, dt: pd.Timestamp) -> pd.Timestamp | None:
    """Return the first day in `idx` strictly after `dt`, or None if none."""
    after = idx[idx > dt]
    return after[0] if len(after) else None


def simulate(
    prices_wide: pd.DataFrame,
    cfg: StrategyConfig | None = None,
    use_regime_overlay: bool = False,
    start: str | None = None,
    end: str | None = None,
    include_cost_sensitivity: bool = True,
    picks_override: dict[str, list[str]] | None = None,
) -> BacktestResult:
    """Run the Phase 1 strategy day-by-day.

    When `use_regime_overlay` is True, the rule-based regime fallback (see
    brain/overlay.py) is applied to scale exposure each month.

    Internally: prices are converted to a numpy array for fast indexing. The
    day-by-day loop is pure Python/numpy — no pandas .loc in the hot path.
    """
    cfg = cfg or StrategyConfig()
    px = prices_wide.copy()
    if start is not None:
        px = px[px.index >= pd.Timestamp(start)]
    if end is not None:
        px = px[px.index <= pd.Timestamp(end)]

    # Pre-compute momentum + vol frames ONCE (used by pick_monthly calls)
    mom_full = compute_momentum(px, cfg.momentum_window)
    vol_full = compute_realized_vol(px, cfg.vol_window)

    r = px.pct_change().fillna(0.0)
    # Rebalance on the LAST TRADING DAY of each period. pandas' resample("ME")
    # labels bins at the calendar month-end (e.g. Nov 30 = Sunday) which is
    # NOT a trading day — using that label directly silently drops ~30% of
    # rebalances. Instead, group trading days by month and take the actual
    # last trading day in each group.
    if cfg.rebalance_freq.upper().startswith("M"):
        period = r.index.to_period("M")
    elif cfg.rebalance_freq.upper().startswith(("W", "FRI")):
        period = r.index.to_period("W")
    elif cfg.rebalance_freq.upper().startswith(("Q")):
        period = r.index.to_period("Q")
    else:
        period = r.index.to_period("M")
    rebal_dates = r.index.to_series().groupby(period).max().values
    rebal_set = set(pd.DatetimeIndex(rebal_dates))

    # Numpy views for speed
    sym_cols = list(px.columns)
    sym_idx = {s: i for i, s in enumerate(sym_cols)}
    px_arr = px.values  # shape (n_days, n_syms)
    mom_arr = mom_full.values
    vol_arr = vol_full.values
    dates = px.index

    # 5d universe return (log) for overlay
    universe_5d_arr = None
    if use_regime_overlay:
        lr = np.log(px).diff().fillna(0.0).values
        uni5 = np.zeros(len(dates))
        cs = np.cumsum(lr, axis=0)
        for i in range(len(dates)):
            if i >= 5:
                uni5[i] = np.nanmean(cs[i] - cs[i - 5])
        universe_5d_arr = uni5

    weights_arr = np.zeros_like(px_arr)

    positions: dict[str, dict] = {}
    trades: list[Trade] = []
    picks_log: list[dict] = []

    current_exposure = 1.0

    for i, dt in enumerate(dates):
        # ------------------------------------------------------------
        # 1. Daily: age positions + check trailing stops
        # ------------------------------------------------------------
        for sym in list(positions.keys()):
            cur_px = px_arr[i, sym_idx[sym]]
            if np.isnan(cur_px):
                continue
            pos = positions[sym]
            pos["hold_days"] += 1
            if cur_px > pos["peak_px"]:
                pos["peak_px"] = cur_px
            if cfg.use_trailing_stop and cur_px <= pos["peak_px"] * (1 - cfg.trailing_stop_pct):
                trades.append(Trade(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=str(dt.date()),
                    entry_px=pos["entry_px"],
                    exit_px=float(cur_px),
                    peak_px=pos["peak_px"],
                    hold_days=pos["hold_days"],
                    ret_pct=float(cur_px) / pos["entry_px"] - 1,
                    exit_reason=f"trailing stop -{int(cfg.trailing_stop_pct*100)}%",
                ))
                del positions[sym]

        # ------------------------------------------------------------
        # 2. Monthly: rebalance target set
        # ------------------------------------------------------------
        if dt in rebal_set:
            # Inline the monthly pick using pre-computed arrays for speed
            mom_row = mom_arr[i]
            vol_row = vol_arr[i]
            valid = ~np.isnan(mom_row)
            market_risk_on = True
            if cfg.market_filter_on:
                if valid.sum() == 0 or np.nanmean(mom_row) < 0:
                    market_risk_on = False

            selected: list[str] = []
            reason = ""
            if not market_risk_on:
                reason = "Market filter: universe mom negative — cash"
            else:
                # vol rank
                order_by_vol = np.argsort(np.where(np.isnan(vol_row), np.inf, vol_row))
                n_valid_vol = int(valid.sum())
                keep_count = int(np.floor(n_valid_vol * cfg.vol_rank_cap))
                keep_mask = np.zeros(len(sym_cols), dtype=bool)
                keep_mask[order_by_vol[:keep_count]] = True
                cand = valid & keep_mask
                if cand.sum() >= cfg.top_n:
                    override_key = str(dt.date())
                    if picks_override is not None and override_key in picks_override:
                        override_syms = [s for s in picks_override[override_key]
                                         if s in sym_cols and cand[sym_idx[s]]]
                        if len(override_syms) >= cfg.top_n:
                            selected = override_syms[:cfg.top_n]
                            reason = f"override (ranker-reranked top-{cfg.top_n})"
                        else:
                            cand_mom = np.where(cand, mom_row, -np.inf)
                            top_ix = np.argsort(-cand_mom)[:cfg.top_n]
                            selected = [sym_cols[j] for j in top_ix]
                            reason = (f"override had {len(override_syms)} valid, "
                                      f"fell back to momentum top-{cfg.top_n}")
                    else:
                        cand_mom = np.where(cand, mom_row, -np.inf)
                        top_ix = np.argsort(-cand_mom)[:cfg.top_n]
                        selected = [sym_cols[j] for j in top_ix]
                        reason = (f"top-{cfg.top_n} by {cfg.momentum_window}d mom, "
                                  f"vol<{int(cfg.vol_rank_cap*100)}%")
                else:
                    reason = f"Too few candidates after vol filter ({int(cand.sum())})"

            picks_log.append({
                "date": str(dt.date()),
                "market_risk_on": market_risk_on,
                "selected": selected,
                "reason": reason,
            })

            if use_regime_overlay and universe_5d_arr is not None:
                u5 = float(universe_5d_arr[i])
                if u5 < -0.10:
                    current_exposure = cfg.exposure_crisis
                elif u5 < -0.05:
                    current_exposure = cfg.exposure_caution
                else:
                    current_exposure = cfg.exposure_normal
            else:
                current_exposure = 1.0

            new_targets = set(selected)
            for sym in list(positions.keys()):
                if sym not in new_targets:
                    pos = positions[sym]
                    cur_px = px_arr[i, sym_idx[sym]]
                    if np.isnan(cur_px):
                        cur_px = pos["entry_px"]
                    trades.append(Trade(
                        symbol=sym,
                        entry_date=pos["entry_date"],
                        exit_date=str(dt.date()),
                        entry_px=pos["entry_px"],
                        exit_px=float(cur_px),
                        peak_px=pos["peak_px"],
                        hold_days=pos["hold_days"],
                        ret_pct=float(cur_px) / pos["entry_px"] - 1,
                        exit_reason="rebalance (rotated out)",
                    ))
                    del positions[sym]
            for sym in new_targets:
                if sym not in positions:
                    cur_px = px_arr[i, sym_idx[sym]]
                    if not np.isnan(cur_px):
                        positions[sym] = {
                            "entry_date": str(dt.date()),
                            "entry_px": float(cur_px),
                            "peak_px": float(cur_px),
                            "hold_days": 0,
                        }

        # ------------------------------------------------------------
        # 3. Write today's target weights
        # ------------------------------------------------------------
        if positions:
            per_weight = current_exposure / cfg.top_n
            for sym in positions:
                weights_arr[i, sym_idx[sym]] = per_weight

    # ----------------------------------------------------------------
    # 4. P&L + costs (vectorised)
    # ----------------------------------------------------------------
    weights = pd.DataFrame(weights_arr, index=dates, columns=sym_cols)
    w = weights.ffill().fillna(0)
    dw = w.diff().abs().fillna(w.abs())
    daily_cost = dw.sum(axis=1) * (cfg.cost_round_trip / 2)
    gross = (w.shift(1) * r).sum(axis=1)
    daily_ret = (gross - daily_cost.shift(1).fillna(0)).fillna(0)
    equity = (1 + daily_ret).cumprod()

    # Benchmark: equal-weight buy-and-hold on the same period
    bench_w = pd.DataFrame(1.0 / len(r.columns), index=r.index, columns=r.columns)
    bench_dw = bench_w.diff().abs().fillna(bench_w.abs())
    bench_daily_cost = bench_dw.sum(axis=1) * (cfg.cost_round_trip / 2)
    bench_gross = (bench_w.shift(1) * r).sum(axis=1)
    bench_ret = (bench_gross - bench_daily_cost.shift(1).fillna(0)).fillna(0)
    bench_eq = (1 + bench_ret).cumprod()

    # ----------------------------------------------------------------
    # 5. Metrics
    # ----------------------------------------------------------------
    metrics = compute_metrics(daily_ret, bench_ret, equity, bench_eq)
    per_year = compute_per_year(daily_ret, bench_ret)
    cost_sens = (compute_cost_sensitivity(prices_wide, cfg, start, end)
                 if include_cost_sensitivity else pd.DataFrame())

    return BacktestResult(
        config=asdict(cfg),
        period=(str(r.index[0].date()), str(r.index[-1].date())),
        daily_returns=daily_ret,
        equity_curve=equity,
        benchmark_curve=bench_eq,
        trades=trades,
        picks_log=picks_log,
        metrics=metrics,
        per_year=per_year,
        cost_sensitivity=cost_sens,
    )


def compute_metrics(rets: pd.Series, bench_rets: pd.Series,
                    equity: pd.Series, bench_eq: pd.Series) -> dict:
    rets = rets.dropna()
    bench_rets = bench_rets.dropna()
    n_days = len(rets)
    years = n_days / 252
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    vol = rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    downside = rets[rets < 0]
    sortino = rets.mean() / downside.std() * np.sqrt(252) if len(downside) and downside.std() > 0 else 0
    dd = (equity / equity.cummax() - 1).min()
    calmar = cagr / abs(dd) if dd < 0 else float("inf")

    # benchmark
    bench_cagr = bench_eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    bench_sharpe = bench_rets.mean() / bench_rets.std() * np.sqrt(252) if bench_rets.std() > 0 else 0
    bench_dd = (bench_eq / bench_eq.cummax() - 1).min()
    alpha_cagr = cagr - bench_cagr

    # rolling 1Y sharpe stability
    roll_sh = rets.rolling(252).apply(
        lambda s: s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
    ).dropna()
    bench_roll_sh = bench_rets.rolling(252).apply(
        lambda s: s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
    ).dropna()
    rolling = pd.concat({"rule": roll_sh, "bh": bench_roll_sh}, axis=1).dropna()

    # Information ratio
    excess = rets - bench_rets
    ir = excess.mean() / excess.std() * np.sqrt(252) if excess.std() > 0 else 0

    return {
        "n_days": n_days,
        "years": round(years, 2),
        "cagr": round(cagr, 4),
        "annualized_vol": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(dd, 4),
        "calmar": round(calmar, 3),
        "information_ratio": round(ir, 3),
        "benchmark_cagr": round(bench_cagr, 4),
        "benchmark_sharpe": round(bench_sharpe, 3),
        "benchmark_max_dd": round(bench_dd, 4),
        "alpha_cagr": round(alpha_cagr, 4),
        "rolling_1y_sharpe_mean": round(float(roll_sh.mean()), 3) if len(roll_sh) else None,
        "rolling_1y_sharpe_min": round(float(roll_sh.min()), 3) if len(roll_sh) else None,
        "rolling_1y_beat_bh_pct": round(float((rolling["rule"] > rolling["bh"]).mean()), 3)
            if len(rolling) else None,
    }


def compute_per_year(rets: pd.Series, bench_rets: pd.Series) -> pd.DataFrame:
    """Calendar-year returns for strategy vs benchmark."""
    rows = []
    for year, gr in rets.groupby(rets.index.year):
        cum = (1 + gr).prod() - 1
        b = bench_rets[bench_rets.index.year == year]
        bcum = (1 + b).prod() - 1
        sh = gr.mean() / gr.std() * np.sqrt(252) if gr.std() > 0 else 0
        bsh = b.mean() / b.std() * np.sqrt(252) if b.std() > 0 else 0
        dd = ((1 + gr).cumprod() / (1 + gr).cumprod().cummax() - 1).min()
        bdd = ((1 + b).cumprod() / (1 + b).cumprod().cummax() - 1).min()
        rows.append({
            "year": year, "rule_ret": cum, "rule_sh": sh, "rule_dd": dd,
            "bh_ret": bcum, "bh_sh": bsh, "bh_dd": bdd,
        })
    return pd.DataFrame(rows).set_index("year")


def compute_cost_sensitivity(prices_wide, cfg: StrategyConfig,
                             start=None, end=None) -> pd.DataFrame:
    """Same strategy at 20/40/60/80/100 bps round-trip costs."""
    rows = []
    for cost in [0.002, 0.004, 0.006, 0.008, 0.010]:
        c = StrategyConfig(**{**asdict(cfg), "cost_round_trip": cost})
        res = simulate(prices_wide, c, use_regime_overlay=False,
                       start=start, end=end, include_cost_sensitivity=False)
        rows.append({
            "round_trip_bps": int(cost * 10000),
            "cagr": res.metrics["cagr"],
            "sharpe": res.metrics["sharpe"],
            "max_dd": res.metrics["max_drawdown"],
        })
    return pd.DataFrame(rows).set_index("round_trip_bps")


# --------------------------------------------------------------------------
# Report writer
# --------------------------------------------------------------------------
def write_report(result: BacktestResult,
                 out_path: Path | None = None) -> Path:
    """Produce a markdown report summarising the backtest."""
    out_path = out_path or REPORT_DIR / "backtest_v2.md"
    m = result.metrics
    lines = []
    lines.append("# PSX Strategy v2 — Backtest Report")
    lines.append(f"\n_Generated: {datetime.now():%Y-%m-%d %H:%M:%S}_")
    lines.append(f"\n**Period:** {result.period[0]} → {result.period[1]} "
                 f"({m['years']:.2f} years, {m['n_days']:,} days)\n")

    lines.append("## Configuration\n")
    for k, v in result.config.items():
        lines.append(f"- `{k}` = {v}")

    lines.append("\n## Headline metrics\n")
    lines.append(f"| Metric | Strategy | Buy & Hold | Delta |")
    lines.append(f"|---|---:|---:|---:|")
    lines.append(f"| CAGR               | {m['cagr']:+.2%} | {m['benchmark_cagr']:+.2%} | {m['alpha_cagr']:+.2%} |")
    lines.append(f"| Sharpe             | {m['sharpe']:.2f} | {m['benchmark_sharpe']:.2f} | {m['sharpe']-m['benchmark_sharpe']:+.2f} |")
    lines.append(f"| Max drawdown       | {m['max_drawdown']:+.2%} | {m['benchmark_max_dd']:+.2%} | {m['max_drawdown']-m['benchmark_max_dd']:+.2%} |")
    lines.append(f"| Sortino            | {m['sortino']:.2f} | — | — |")
    lines.append(f"| Calmar             | {m['calmar']:.2f} | — | — |")
    lines.append(f"| Information ratio  | {m['information_ratio']:.2f} | — | — |")
    lines.append(f"| Rolling 1Y Sh mean | {m['rolling_1y_sharpe_mean']} | — | — |")
    lines.append(f"| Rolling 1Y Sh min  | {m['rolling_1y_sharpe_min']} | — | — |")
    lines.append(f"| % 1Y windows beating B&H | {m['rolling_1y_beat_bh_pct']} | — | — |")

    lines.append("\n## Per calendar-year\n")
    lines.append("| Year | Rule ret | Rule Sh | Rule DD | B&H ret | B&H Sh | B&H DD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for yr, row in result.per_year.iterrows():
        lines.append(f"| {yr} | {row['rule_ret']:+.2%} | {row['rule_sh']:.2f} | "
                     f"{row['rule_dd']:+.2%} | {row['bh_ret']:+.2%} | "
                     f"{row['bh_sh']:.2f} | {row['bh_dd']:+.2%} |")

    lines.append("\n## Cost sensitivity\n")
    lines.append("| Round-trip | CAGR | Sharpe | Max DD |")
    lines.append("|---:|---:|---:|---:|")
    for bps, row in result.cost_sensitivity.iterrows():
        lines.append(f"| {bps} bps | {row['cagr']:+.2%} | {row['sharpe']:.2f} | "
                     f"{row['max_dd']:+.2%} |")

    lines.append("\n## Trade summary\n")
    if result.trades:
        tr = pd.DataFrame([asdict(t) for t in result.trades])
        wins = tr[tr["ret_pct"] > 0]
        losses = tr[tr["ret_pct"] <= 0]
        win_rate = len(wins) / len(tr) if len(tr) else 0
        avg_ret = tr["ret_pct"].mean()
        avg_hold = tr["hold_days"].mean()
        pf = (wins["ret_pct"].sum() / abs(losses["ret_pct"].sum())
              if len(losses) and losses["ret_pct"].sum() != 0 else float("inf"))
        lines.append(f"- Total closed trades: {len(tr)}")
        lines.append(f"- Win rate: {win_rate:.1%}")
        lines.append(f"- Avg return/trade: {avg_ret:+.2%}")
        lines.append(f"- Avg hold: {avg_hold:.1f} days")
        lines.append(f"- Profit factor: {pf:.2f}")

        lines.append("\n### Exit-reason breakdown\n")
        rc = tr.groupby("exit_reason").agg(
            n=("symbol", "count"),
            avg_ret=("ret_pct", "mean"),
            avg_hold=("hold_days", "mean"),
        ).round(3)
        lines.append("| Exit reason | N | Avg return | Avg hold |")
        lines.append("|---|---:|---:|---:|")
        for reason, row in rc.iterrows():
            lines.append(f"| {reason} | {int(row['n'])} | "
                         f"{row['avg_ret']:+.2%} | {row['avg_hold']:.1f} |")

        lines.append("\n### Per-symbol P&L\n")
        ps = tr.groupby("symbol").agg(
            n=("symbol", "count"),
            total_ret=("ret_pct", "sum"),
            avg_ret=("ret_pct", "mean"),
            win_rate=("ret_pct", lambda s: (s > 0).mean()),
        ).sort_values("total_ret", ascending=False).round(3)
        lines.append("| Symbol | N | Total ret | Avg ret | Win rate |")
        lines.append("|---|---:|---:|---:|---:|")
        for sym, row in ps.iterrows():
            lines.append(f"| {sym} | {int(row['n'])} | "
                         f"{row['total_ret']:+.2%} | {row['avg_ret']:+.2%} | "
                         f"{row['win_rate']:.1%} |")

    lines.append("\n## Monthly picks log\n")
    lines.append("| Month | Risk-on | Selected | Reason |")
    lines.append("|---|---|---|---|")
    for rec in result.picks_log:
        picks = ", ".join(rec["selected"]) if rec["selected"] else "CASH"
        lines.append(f"| {rec['date']} | "
                     f"{'yes' if rec['market_risk_on'] else 'no'} | "
                     f"{picks} | {rec['reason']} |")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    # Also persist the raw metrics JSON for programmatic use
    metrics_path = out_path.with_suffix(".json")
    metrics_path.write_text(
        json.dumps({"metrics": result.metrics, "config": result.config,
                    "period": list(result.period)}, indent=2),
        encoding="utf-8",
    )
    return out_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    console = Console()
    console.print("[bold]PSX Strategy v2 — Backtest[/bold]")
    wide = build_prices_wide(universe_symbols())
    console.print(f"Loaded {len(wide)} days × {len(wide.columns)} symbols "
                  f"({wide.index.min().date()} → {wide.index.max().date()})")

    cfg = StrategyConfig()
    console.print(f"\nConfig: {cfg}\n")

    console.print("[cyan]Running core rule (no overlay)...[/cyan]")
    res = simulate(wide, cfg, use_regime_overlay=False)
    m = res.metrics

    t = Table(title="Headline metrics", show_header=True)
    t.add_column("Metric"); t.add_column("Rule"); t.add_column("B&H")
    t.add_row("CAGR", f"{m['cagr']:+.2%}", f"{m['benchmark_cagr']:+.2%}")
    t.add_row("Sharpe", f"{m['sharpe']:.2f}", f"{m['benchmark_sharpe']:.2f}")
    t.add_row("Max DD", f"{m['max_drawdown']:+.2%}", f"{m['benchmark_max_dd']:+.2%}")
    t.add_row("Sortino", f"{m['sortino']:.2f}", "-")
    t.add_row("Calmar", f"{m['calmar']:.2f}", "-")
    t.add_row("Info ratio", f"{m['information_ratio']:.2f}", "-")
    t.add_row("Trades", f"{len(res.trades)}", "0")
    t.add_row("% 1Y windows beating B&H",
              f"{m['rolling_1y_beat_bh_pct']:.0%}" if m['rolling_1y_beat_bh_pct'] is not None else "-",
              "-")
    console.print(t)

    # Optional overlay variant for comparison
    console.print("\n[cyan]Running with rule-based regime overlay...[/cyan]")
    res2 = simulate(wide, cfg, use_regime_overlay=True)
    m2 = res2.metrics
    t2 = Table(title="With regime overlay")
    t2.add_column("Metric"); t2.add_column("Core rule"); t2.add_column("+ overlay")
    t2.add_row("CAGR", f"{m['cagr']:+.2%}", f"{m2['cagr']:+.2%}")
    t2.add_row("Sharpe", f"{m['sharpe']:.2f}", f"{m2['sharpe']:.2f}")
    t2.add_row("Max DD", f"{m['max_drawdown']:+.2%}", f"{m2['max_drawdown']:+.2%}")
    t2.add_row("Calmar", f"{m['calmar']:.2f}", f"{m2['calmar']:.2f}")
    console.print(t2)

    report_path = write_report(res, REPORT_DIR / "backtest_v2_core.md")
    write_report(res2, REPORT_DIR / "backtest_v2_with_overlay.md")
    console.print(f"\n[green]Reports written to {report_path.parent}[/green]")

    return res, res2


if __name__ == "__main__":
    main()
