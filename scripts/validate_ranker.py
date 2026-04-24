"""Phase 2: A/B test the cross-sectional ranker against Phase 1.

Decision rule (deployment gate)
-------------------------------
Deploy `brain/ranker.py` ONLY IF the combined out-of-sample backtest shows:
  - CAGR(ranker) >= CAGR(phase1) + 2.0 percentage points, AND
  - MaxDD(ranker) is no worse than MaxDD(phase1) + 3 percentage points
  - Sharpe(ranker) >= Sharpe(phase1)

This is a *hard* gate. The default behaviour of the daily runner is
`ranker_enabled=False`. We only flip it when this script prints PASS.

Methodology
-----------
1. Build the full stacked dataset from the universe's wide price frame.
2. Run purged walk-forward CV (20-day embargo) → out-of-sample predictions
   for every (date, symbol) after the initial train window.
3. For each rebalance date, take Phase 1's volatility-filtered candidate set
   (top 70% by low vol) and re-rank those candidates by the OOS predictions,
   picking top_n.
4. Run `backtest_v2.simulate` twice on the identical period:
     a. baseline (default Phase 1)
     b. with `picks_override` from step 3
5. Compare metrics side-by-side and emit PASS/FAIL.

Output
------
- reports/validate_ranker_<ts>.md    (full report)
- models/ranker_v2.pkl               (only saved if PASS)
- models/ranker_enabled.json         (written with the PASS/FAIL verdict)

Usage:
    python scripts/validate_ranker.py
    python scripts/validate_ranker.py --start 2022-01-01
    python scripts/validate_ranker.py --top-n 5 --splits 6
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from brain.backtest_v2 import simulate
from brain.ranker import (
    RankerConfig, build_ranker_dataset, walk_forward_validate, save_ranker,
    train_ranker,
)
from brain.strategy import (
    StrategyConfig, build_prices_wide, compute_momentum, compute_realized_vol,
)
from config.universe import symbols as universe_symbols


REPORT_DIR = PROJECT_ROOT / "reports"
MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)

CAGR_THRESHOLD_PP = 2.0          # need +2 pp CAGR to deploy
DD_TOLERANCE_PP = 3.0            # DD may worsen by at most 3 pp
SHARPE_MIN_DELTA = 0.0           # Sharpe must not get worse


def build_rerank_picks(
    oos: pd.DataFrame,
    prices_wide: pd.DataFrame,
    cfg: StrategyConfig,
) -> dict[str, list[str]]:
    """For each month-end rebalance date, produce the ranker-reranked top-N."""
    r = prices_wide.pct_change().fillna(0.0)
    rebal_dates = r.resample(cfg.rebalance_freq).last().index

    mom_full = compute_momentum(prices_wide, cfg.momentum_window)
    vol_full = compute_realized_vol(prices_wide, cfg.vol_window)

    oos["date"] = pd.to_datetime(oos["date"])
    oos_by_date = {d: g for d, g in oos.groupby("date")}

    picks: dict[str, list[str]] = {}
    for dt in rebal_dates:
        if dt not in mom_full.index:
            continue
        mom_row = mom_full.loc[dt]
        vol_row = vol_full.loc[dt]
        valid = mom_row.notna() & vol_row.notna()
        if valid.sum() < cfg.top_n:
            continue
        # Vol filter identical to Phase 1
        vol_rank = vol_row[valid].rank(pct=True)
        keep = vol_rank[vol_rank <= cfg.vol_rank_cap].index

        if dt not in oos_by_date:
            continue
        preds = oos_by_date[dt].set_index("symbol")["y_pred"]
        reranked = preds.reindex(keep).dropna().sort_values(ascending=False)

        if len(reranked) >= cfg.top_n:
            picks[str(dt.date())] = reranked.head(cfg.top_n).index.tolist()
    return picks


def make_metric_table(
    base: dict, rerank: dict, per_base, per_rerank,
) -> Table:
    t = Table(title="Phase 1 vs Phase 1 + Ranker (OOS)")
    t.add_column("Metric")
    t.add_column("Phase 1", justify="right")
    t.add_column("Ranker", justify="right")
    t.add_column("Δ", justify="right")

    def row(name, a, b, fmt="{:+.2%}"):
        try:
            delta = b - a
        except TypeError:
            delta = "—"
        d = fmt.format(delta) if isinstance(delta, (int, float)) else delta
        t.add_row(name,
                  fmt.format(a) if isinstance(a, (int, float)) else str(a),
                  fmt.format(b) if isinstance(b, (int, float)) else str(b),
                  d)

    row("CAGR", base["cagr"], rerank["cagr"])
    row("Sharpe", base["sharpe"], rerank["sharpe"], "{:+.2f}")
    row("Sortino", base["sortino"], rerank["sortino"], "{:+.2f}")
    row("Max drawdown", base["max_drawdown"], rerank["max_drawdown"])
    row("Calmar", base["calmar"], rerank["calmar"], "{:+.2f}")
    return t


def verdict(base: dict, rerank: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    cagr_delta_pp = (rerank["cagr"] - base["cagr"]) * 100
    dd_delta_pp = (rerank["max_drawdown"] - base["max_drawdown"]) * 100
    sharpe_delta = rerank["sharpe"] - base["sharpe"]

    if cagr_delta_pp >= CAGR_THRESHOLD_PP:
        reasons.append(f"[PASS] CAGR +{cagr_delta_pp:.2f}pp (need +{CAGR_THRESHOLD_PP:.1f})")
        cagr_ok = True
    else:
        reasons.append(f"[FAIL] CAGR only {cagr_delta_pp:+.2f}pp (need +{CAGR_THRESHOLD_PP:.1f})")
        cagr_ok = False

    if dd_delta_pp >= -DD_TOLERANCE_PP:
        reasons.append(f"[PASS] MaxDD moved {dd_delta_pp:+.2f}pp (tolerance -{DD_TOLERANCE_PP:.1f})")
        dd_ok = True
    else:
        reasons.append(f"[FAIL] MaxDD worsened {dd_delta_pp:+.2f}pp (tolerance -{DD_TOLERANCE_PP:.1f})")
        dd_ok = False

    if sharpe_delta >= SHARPE_MIN_DELTA:
        reasons.append(f"[PASS] Sharpe {sharpe_delta:+.3f} (need >= {SHARPE_MIN_DELTA})")
        sh_ok = True
    else:
        reasons.append(f"[FAIL] Sharpe {sharpe_delta:+.3f} (need >= {SHARPE_MIN_DELTA})")
        sh_ok = False

    return (cagr_ok and dd_ok and sh_ok, reasons)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="Override backtest start date")
    ap.add_argument("--end", default=None, help="Override backtest end date")
    ap.add_argument("--top-n", type=int, default=None, help="Override top_n")
    ap.add_argument("--splits", type=int, default=5, help="Walk-forward splits")
    args = ap.parse_args()

    console = Console()

    cfg = StrategyConfig()
    if args.top_n is not None:
        cfg.top_n = args.top_n

    console.rule("[bold cyan]Phase 2 validator — ranker A/B test")

    console.print("Loading universe prices...")
    wide = build_prices_wide(universe_symbols())
    console.print(f"  {wide.shape[0]} days × {wide.shape[1]} symbols")

    console.print("Building ranker dataset...")
    X, y, meta = build_ranker_dataset(wide)
    console.print(f"  X={X.shape}, y.mean={y.mean():+.4f}, y.std={y.std():.4f}")

    rc = RankerConfig(n_splits=args.splits)
    console.print(f"Running purged walk-forward ({rc.n_splits} splits, "
                  f"{rc.embargo_days}d embargo)...")
    oos = walk_forward_validate(X, y, meta, rc)
    console.print(f"  OOS predictions: {len(oos):,} rows across "
                  f"{oos['date'].nunique()} dates")

    ic_overall = oos[["y_true", "y_pred"]].corr().iloc[0, 1]
    ic_daily = (oos.groupby("date")
                   .apply(lambda g: g["y_true"].corr(g["y_pred"]) if g["y_true"].std() > 0 else np.nan,
                          include_groups=False)
                   .dropna())
    console.print(f"  IC (overall, all rows pooled) = {ic_overall:+.4f}")
    console.print(f"  IC (per-day mean)             = {ic_daily.mean():+.4f}  "
                  f"(std {ic_daily.std():.4f}, n={len(ic_daily)})")

    picks_override = build_rerank_picks(oos, wide, cfg)
    console.print(f"Re-ranked picks for {len(picks_override)} month-ends")

    common_start = oos["date"].min().strftime("%Y-%m-%d") if args.start is None else args.start
    common_end = args.end
    console.print(f"A/B period: {common_start} → {common_end or wide.index[-1].date()}")

    console.print("Running Phase 1 baseline...")
    base = simulate(wide, cfg, start=common_start, end=common_end,
                    include_cost_sensitivity=False)
    console.print("Running Phase 1 + ranker...")
    rerank = simulate(wide, cfg, start=common_start, end=common_end,
                      include_cost_sensitivity=False,
                      picks_override=picks_override)

    bm, rm = base.metrics, rerank.metrics
    console.print()
    console.print(make_metric_table(bm, rm, base.per_year, rerank.per_year))

    passed, reasons = verdict(bm, rm)
    console.print()
    for r in reasons:
        console.print(f"  {r}")

    console.print()
    if passed:
        console.rule("[bold green]VERDICT: DEPLOY RANKER")
        booster = train_ranker(X, y, meta, rc)
        save_ranker(booster)
        with open(MODELS_DIR / "ranker_enabled.json", "w") as f:
            json.dump({"enabled": True, "verdict_at": datetime.now().isoformat(),
                       "reasons": reasons, "metrics_base": bm,
                       "metrics_ranker": rm}, f, indent=2)
    else:
        console.rule("[bold yellow]VERDICT: DO NOT DEPLOY (keep Phase 1)")
        with open(MODELS_DIR / "ranker_enabled.json", "w") as f:
            json.dump({"enabled": False, "verdict_at": datetime.now().isoformat(),
                       "reasons": reasons, "metrics_base": bm,
                       "metrics_ranker": rm}, f, indent=2)

    # Write markdown
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORT_DIR / f"validate_ranker_{ts}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Ranker validation — {datetime.now():%Y-%m-%d %H:%M}\n\n")
        f.write(f"Period: `{common_start}` → `{common_end or wide.index[-1].date()}`\n\n")
        f.write(f"IC (pooled): **{ic_overall:+.4f}**  ·  IC (daily mean): "
                f"**{ic_daily.mean():+.4f}** (n={len(ic_daily)})\n\n")
        f.write("## Headline\n\n")
        f.write("| Metric | Phase 1 | Ranker | Δ |\n|---|---:|---:|---:|\n")
        f.write(f"| CAGR       | {bm['cagr']:+.2%} | {rm['cagr']:+.2%} | {(rm['cagr']-bm['cagr'])*100:+.2f} pp |\n")
        f.write(f"| Sharpe     | {bm['sharpe']:.2f} | {rm['sharpe']:.2f} | {rm['sharpe']-bm['sharpe']:+.2f} |\n")
        f.write(f"| Sortino    | {bm['sortino']:.2f} | {rm['sortino']:.2f} | {rm['sortino']-bm['sortino']:+.2f} |\n")
        f.write(f"| Max DD     | {bm['max_drawdown']:+.2%} | {rm['max_drawdown']:+.2%} | {(rm['max_drawdown']-bm['max_drawdown'])*100:+.2f} pp |\n")
        f.write(f"| Calmar     | {bm['calmar']:.2f} | {rm['calmar']:.2f} | {rm['calmar']-bm['calmar']:+.2f} |\n\n")
        f.write("## Verdict\n\n")
        f.write(f"**{'DEPLOY' if passed else 'DO NOT DEPLOY'}**\n\n")
        for r in reasons:
            f.write(f"- {r}\n")
    console.print(f"\n[dim]Report saved to {out}[/dim]")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
