"""Walk-forward backtester for the per-stock ensemble.

Strategy (simple, transparent):
  - Each day, compute out-of-sample ensemble probability P(up) for each symbol.
  - BUY when P(up) crosses above `entry_threshold` (e.g. 0.55) and no position.
  - EXIT when either:
      * P(up) drops below `exit_threshold` (e.g. 0.45), or
      * trailing stop hits (move price drops `stop_pct` from peak), or
      * max holding period (`max_hold_days`) reached.
  - Equal-weight across positions; max `max_positions` concurrent.
  - Execution: enter/exit on NEXT day's OPEN (realistic one-day delay).
  - Cost model: 0.10% commission + 0.10% slippage each side = 0.40% round-trip.

Benchmarks vs: KSE-100 buy-and-hold (via yfinance proxy) AND equal-weight
buy-and-hold of our 15-stock universe.

Reports:
    reports/backtest_{timestamp}.md
    per-position trade log, equity curve CSV, summary stats
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from brain.features import build_features, feature_columns
from brain.models import walkforward_predict
from config.universe import symbols as universe_symbols, sector_of
from data.store import load_ohlcv


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    entry_threshold: float = 0.55
    exit_threshold: float = 0.45
    stop_pct: float = 0.08          # 8% trailing stop
    max_hold_days: int = 15          # max holding period
    max_positions: int = 6           # concurrent holdings
    max_per_sector: int = 2          # sector diversification cap
    commission_bps: float = 10       # 0.10%
    slippage_bps: float = 10         # 0.10%
    initial_capital: float = 1_000_000  # PKR 10 lakh starting book
    cash_yield_annual: float = 0.11   # ~policy rate on idle cash

    # Sizing — conviction + vol weighted ----------------------------------
    # When True: position_size_pct = clip(base * conviction / vol_scale, min, max)
    # When False: legacy equal-weight (cash / free_slots)
    conviction_sizing: bool = True
    vol_target_daily: float = 0.02       # target realized daily vol for "normal" sizing
    min_pos_pct: float = 0.04            # min 4% per position
    max_pos_pct: float = 0.18            # max 18% per position
    base_pos_pct: float = 0.12           # base 12% at prob=0.65 & normal vol

    # Economic gate — exclude symbols whose walk-forward expected
    # per-trade return is below 2x round-trip cost.
    apply_economic_gate: bool = True
    econ_gate_mult_of_cost: float = 2.0  # trade avg return must exceed this * round-trip cost


@dataclass
class Trade:
    symbol: str
    sector: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_px: float
    exit_px: float
    size_pkr: float
    shares: int
    pnl_pkr: float
    return_pct: float
    hold_days: int
    exit_reason: str
    entry_prob: float


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    equity_curve: pd.DataFrame
    benchmark_curve: pd.DataFrame
    stats: dict
    oos_signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    economics: pd.DataFrame = field(default_factory=pd.DataFrame)
    blocked_by_gate: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _build_next_open_frame(symbols_: list[str]) -> pd.DataFrame:
    """For each symbol, add a 'next_open' column: tomorrow's open, used for fills."""
    frames = []
    for s in symbols_:
        df = load_ohlcv(s)[["date", "symbol", "open", "close"]]
        df["next_open"] = df["open"].shift(-1)
        frames.append(df)
    big = pd.concat(frames, ignore_index=True)
    big["date"] = pd.to_datetime(big["date"])
    return big


def _universe_signature(symbols: list[str]) -> str:
    """Stable 10-char hash of the sorted universe. Invalidates cache on any change."""
    key = "|".join(sorted(symbols))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def _signal_cache_path(signature: str) -> Path:
    return PROJECT_ROOT / "data" / f"walkforward_signals_{signature}.parquet"


def _build_signals(feat: pd.DataFrame, feature_cols: list[str],
                    use_cache: bool = True) -> pd.DataFrame:
    """Run walk-forward predictions for every symbol and stack the results.

    Cache key includes a hash of the universe, so a universe change
    automatically invalidates the cache. Old caches under a prior universe
    stay on disk (cheap) in case you switch back.
    """
    symbols = sorted(feat.symbol.unique().tolist())
    sig_hash = _universe_signature(symbols)
    cache = _signal_cache_path(sig_hash)

    if use_cache and cache.exists():
        cached = pd.read_parquet(cache)
        cached["date"] = pd.to_datetime(cached["date"])
        cached_syms = set(cached.symbol.unique())
        if cached_syms == set(symbols):
            return cached
        # Signature collision (extremely unlikely) or cache tampered — rebuild
        cache.unlink(missing_ok=True)

    out = []
    for s in symbols:
        res = walkforward_predict(feat, s, feature_cols, n_splits=5)
        if not res.empty:
            out.append(res)
    if not out:
        return pd.DataFrame()
    sig = pd.concat(out, ignore_index=True).sort_values(["date", "symbol"])
    try:
        sig.to_parquet(cache, engine="pyarrow", index=False)
    except Exception:
        pass
    return sig


# --------------------------------------------------------------------------
# Per-symbol walk-forward economics — "does this stock actually PROFIT?"
# --------------------------------------------------------------------------
def _realized_vol_by_symbol(feat: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Realized daily log-return vol over `window` days, per (symbol, date)."""
    f = feat[["date", "symbol", "close"]].copy()
    f["date"] = pd.to_datetime(f["date"])
    f = f.sort_values(["symbol", "date"])
    f["logret"] = np.log(f["close"]).groupby(f["symbol"]).diff()
    f["vol_d"] = (f.groupby("symbol")["logret"]
                    .transform(lambda s: s.rolling(window, min_periods=5).std()))
    return f[["date", "symbol", "vol_d"]]


def compute_per_symbol_economics(
    signals: pd.DataFrame,
    feat: pd.DataFrame,
    cfg: BacktestConfig,
) -> pd.DataFrame:
    """For every symbol, simulate entries/exits IN ISOLATION using its own signals.

    Returns a DataFrame with one row per symbol:
        n_trades, win_rate, avg_return, total_return, profit_factor,
        sharpe_trade, median_return, economically_viable (bool)

    This is what we use to gate the live universe — "training AUC says
    predictive" but "avg return per trade after costs > 2x cost" is the
    test of whether it's actually worth trading.
    """
    px = _build_next_open_frame(sorted(signals.symbol.unique().tolist()))
    merged = signals.merge(
        px[["date", "symbol", "next_open"]],
        on=["date", "symbol"],
        how="left",
    ).dropna(subset=["next_open", "close"]).sort_values(["date", "symbol"])

    round_trip_cost = 2 * (cfg.commission_bps + cfg.slippage_bps) / 10_000
    threshold = cfg.econ_gate_mult_of_cost * round_trip_cost

    rows = []
    for sym in sorted(signals.symbol.unique()):
        sdf = merged[merged.symbol == sym].reset_index(drop=True)
        position = None
        trade_rets: list[float] = []
        for _, r in sdf.iterrows():
            if position is None:
                if r.prob_up_oos >= cfg.entry_threshold:
                    entry_px = float(r.next_open) * (1 + cfg.slippage_bps / 10_000)
                    position = {"entry": entry_px, "peak": entry_px, "days": 0}
            else:
                cur = float(r["close"])
                position["peak"] = max(position["peak"], cur)
                position["days"] += 1
                stop_px = position["peak"] * (1 - cfg.stop_pct)
                exit_reason = None
                if cur <= stop_px and position["peak"] > position["entry"]:
                    exit_reason = "trailing_stop"
                elif r.prob_up_oos < cfg.exit_threshold:
                    exit_reason = "signal_exit"
                elif position["days"] >= cfg.max_hold_days:
                    exit_reason = "max_hold"
                if exit_reason:
                    exit_px = float(r.next_open) * (1 - cfg.slippage_bps / 10_000)
                    gross_ret = exit_px / position["entry"] - 1
                    net_ret = gross_ret - (2 * cfg.commission_bps / 10_000)
                    trade_rets.append(net_ret)
                    position = None
        n = len(trade_rets)
        arr = np.array(trade_rets) if n else np.array([0.0])
        wins = (arr > 0).sum()
        avg_ret = float(arr.mean())
        sharpe = float(arr.mean() / arr.std() * np.sqrt(50)) if n > 1 and arr.std() > 0 else 0
        pos_sum = float(arr[arr > 0].sum())
        neg_sum = float(-arr[arr < 0].sum())
        pf = (pos_sum / neg_sum) if neg_sum > 0 else float("inf")
        rows.append({
            "symbol": sym,
            "n_trades": n,
            "win_rate": float(wins / n) if n else 0.0,
            "avg_return": avg_ret,
            "median_return": float(np.median(arr)) if n else 0.0,
            "total_return": float(arr.sum()),
            "profit_factor": round(pf, 2) if np.isfinite(pf) else 99.0,
            "sharpe_trade": round(sharpe, 2),
            "round_trip_cost": round_trip_cost,
            "threshold": threshold,
            "economically_viable": avg_ret >= threshold,
        })
    econ = pd.DataFrame(rows).sort_values("avg_return", ascending=False)
    return econ


def save_economics(econ: pd.DataFrame) -> Path:
    """Persist economics so the daily risk manager can use it."""
    out = PROJECT_ROOT / "models" / "economic_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": econ.to_dict(orient="records"),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# Core simulator
# --------------------------------------------------------------------------
def run_backtest(cfg: BacktestConfig = BacktestConfig()) -> BacktestResult:
    """Simulate the strategy on walk-forward out-of-sample signals."""
    feat = build_features(include_macro=True)
    cols = feature_columns(feat)

    signals = _build_signals(feat, cols)
    if signals.empty:
        raise RuntimeError("No signals generated")

    # --- Per-symbol economics (drives live-trading gate) ---
    econ = compute_per_symbol_economics(signals, feat, cfg)
    save_economics(econ)
    viable = set(econ.loc[econ.economically_viable, "symbol"].tolist())
    blocked_by_gate = set(econ.loc[~econ.economically_viable, "symbol"].tolist())

    # --- Realized vol, for conviction-weighted sizing ---
    vol_df = _realized_vol_by_symbol(feat, window=20)

    px = _build_next_open_frame(sorted(feat.symbol.unique().tolist()))
    merged = signals.merge(
        px[["date", "symbol", "next_open"]],
        on=["date", "symbol"],
        how="left",
    ).merge(vol_df, on=["date", "symbol"], how="left")
    merged = merged.dropna(subset=["next_open", "close"]).sort_values(["date", "symbol"])

    # --- State ---
    cash = cfg.initial_capital
    positions: dict[str, dict] = {}
    trades: list[Trade] = []
    equity_rows: list[dict] = []

    all_dates = sorted(merged["date"].unique())

    for d in all_dates:
        day = merged[merged["date"] == d]

        # --- 1. Update open positions (mark-to-market, check stops) ---
        for sym in list(positions.keys()):
            row = day[day["symbol"] == sym]
            if row.empty:
                continue
            cur_close = float(row["close"].iloc[0])
            pos = positions[sym]
            pos["peak"] = max(pos["peak"], cur_close)
            pos["days"] += 1
            cur_prob = float(row["prob_up_oos"].iloc[0])
            pos["last_prob"] = cur_prob

            stop_px = pos["peak"] * (1 - cfg.stop_pct)
            exit_reason = None

            if cur_close <= stop_px and pos["peak"] > pos["entry_px"]:
                exit_reason = "trailing_stop"
            elif cur_prob < cfg.exit_threshold:
                exit_reason = "signal_exit"
            elif pos["days"] >= cfg.max_hold_days:
                exit_reason = "max_hold"

            if exit_reason:
                next_open = row["next_open"].iloc[0]
                if pd.isna(next_open):
                    continue  # last bar, defer
                exit_px = float(next_open) * (1 - cfg.slippage_bps / 10_000)
                gross = pos["shares"] * exit_px
                fee = gross * (cfg.commission_bps / 10_000)
                cash += gross - fee
                pnl = (exit_px - pos["entry_px"]) * pos["shares"] - pos["entry_fee"] - fee
                trades.append(Trade(
                    symbol=sym,
                    sector=sector_of(sym) or "Other",
                    entry_date=pos["entry_date"],
                    exit_date=d + pd.Timedelta(days=1),
                    entry_px=pos["entry_px"],
                    exit_px=exit_px,
                    size_pkr=pos["size_pkr"],
                    shares=pos["shares"],
                    pnl_pkr=pnl,
                    return_pct=pnl / pos["size_pkr"],
                    hold_days=pos["days"],
                    exit_reason=exit_reason,
                    entry_prob=pos["entry_prob"],
                ))
                del positions[sym]

        # --- 2. Consider new entries ---
        free_slots = cfg.max_positions - len(positions)
        if free_slots > 0:
            # Entry mask: prob >= threshold, not held, (optional) economic gate pass
            mask = (
                (day["prob_up_oos"] >= cfg.entry_threshold)
                & (~day["symbol"].isin(positions.keys()))
            )
            if cfg.apply_economic_gate:
                mask &= day["symbol"].isin(viable)
            cands = day[mask].sort_values("prob_up_oos", ascending=False)

            # Sector cap
            sector_counts: dict[str, int] = {}
            for sym in positions:
                s = sector_of(sym) or "Other"
                sector_counts[s] = sector_counts.get(s, 0) + 1

            # Current equity (for % sizing)
            cur_equity = cash + sum(
                pos["shares"] * float(day[day.symbol == s]["close"].iloc[0])
                for s, pos in positions.items()
                if not day[day.symbol == s].empty
            )

            for _, r in cands.iterrows():
                if free_slots <= 0:
                    break
                sym = r["symbol"]
                sec = sector_of(sym) or "Other"
                if sector_counts.get(sec, 0) >= cfg.max_per_sector:
                    continue
                next_open = r["next_open"]
                if pd.isna(next_open):
                    continue

                # --- Sizing ---
                if cfg.conviction_sizing:
                    # Conviction: (prob - 0.5) maps [0.55, 1.0] → [0.05, 0.5]
                    conviction = max(0.0, float(r["prob_up_oos"]) - 0.5)
                    # Vol scaling: low-vol names get full size; high-vol get penalized
                    vol_d = r.get("vol_d")
                    if pd.isna(vol_d) or vol_d is None or vol_d <= 0:
                        vol_d = cfg.vol_target_daily
                    vol_scale = cfg.vol_target_daily / float(vol_d)
                    # Base = 12% at conviction=0.15 (prob=0.65) & vol_scale=1
                    raw_pct = cfg.base_pos_pct * (conviction / 0.15) * vol_scale
                    pos_pct = max(cfg.min_pos_pct, min(cfg.max_pos_pct, raw_pct))
                    alloc = cur_equity * pos_pct
                else:
                    alloc = cash / max(free_slots, 1)
                if alloc < 10_000 or alloc > cash:
                    # Under-capitalized or can't afford → try next
                    if alloc > cash:
                        alloc = cash * 0.95
                    if alloc < 10_000:
                        continue
                entry_px = float(next_open) * (1 + cfg.slippage_bps / 10_000)
                shares = int(alloc // entry_px)
                if shares <= 0:
                    continue
                gross = shares * entry_px
                fee = gross * (cfg.commission_bps / 10_000)
                if gross + fee > cash:
                    continue
                cash -= (gross + fee)

                positions[sym] = {
                    "entry_date": d + pd.Timedelta(days=1),
                    "entry_px": entry_px,
                    "entry_fee": fee,
                    "shares": shares,
                    "peak": entry_px,
                    "days": 0,
                    "entry_prob": float(r["prob_up_oos"]),
                    "last_prob": float(r["prob_up_oos"]),
                    "size_pkr": gross,
                }
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
                free_slots -= 1

        # --- 3. Mark equity ---
        pos_value = 0.0
        for sym, pos in positions.items():
            row = day[day["symbol"] == sym]
            if not row.empty:
                pos_value += pos["shares"] * float(row["close"].iloc[0])
            else:
                pos_value += pos["shares"] * pos["entry_px"]
        # Accrue cash yield on idle capital
        daily_rate = (1 + cfg.cash_yield_annual) ** (1 / 252) - 1
        cash *= (1 + daily_rate)
        total = cash + pos_value
        equity_rows.append({
            "date": d,
            "cash": cash,
            "positions_value": pos_value,
            "equity": total,
            "n_positions": len(positions),
        })

    equity_curve = pd.DataFrame(equity_rows)

    # --- Benchmark: equal-weight buy-and-hold of the universe ---
    benches = []
    for s in feat.symbol.unique():
        df = load_ohlcv(s)[["date", "close"]].rename(columns={"close": s})
        df["date"] = pd.to_datetime(df["date"])
        benches.append(df.set_index("date"))
    bench_wide = pd.concat(benches, axis=1).ffill().dropna(how="all")
    first = bench_wide.iloc[0]
    bench_norm = bench_wide.div(first) * (cfg.initial_capital / len(first))
    bench_curve = bench_norm.sum(axis=1).reset_index()
    bench_curve.columns = ["date", "equity"]
    bench_curve = bench_curve[bench_curve["date"] >= equity_curve["date"].min()]

    # --- Stats ---
    stats = _compute_stats(equity_curve, bench_curve, trades, cfg)

    return BacktestResult(
        config=cfg,
        trades=trades,
        equity_curve=equity_curve,
        benchmark_curve=bench_curve,
        stats=stats,
        oos_signals=signals,
        economics=econ,
        blocked_by_gate=sorted(blocked_by_gate),
    )


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def _compute_stats(equity: pd.DataFrame, bench: pd.DataFrame,
                   trades: list[Trade], cfg: BacktestConfig) -> dict:
    eq = equity.sort_values("date").reset_index(drop=True).copy()
    eq["date"] = pd.to_datetime(eq["date"])
    daily_ret = eq["equity"].pct_change().dropna()

    days = len(eq)
    years = days / 252.0 if days else 1.0
    final = eq["equity"].iloc[-1]
    total_ret = final / cfg.initial_capital - 1
    cagr = (final / cfg.initial_capital) ** (1 / years) - 1 if years > 0 else 0

    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    downside = daily_ret[daily_ret < 0]
    sortino = (daily_ret.mean() / downside.std() * np.sqrt(252)) if len(downside) and downside.std() > 0 else 0

    roll_max = eq["equity"].cummax()
    dd = eq["equity"] / roll_max - 1
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf")

    # Bench
    bench = bench.sort_values("date").reset_index(drop=True).copy()
    bench["date"] = pd.to_datetime(bench["date"])
    bench_ret = bench["equity"].iloc[-1] / bench["equity"].iloc[0] - 1 if len(bench) else 0
    bench_years = len(bench) / 252.0 if len(bench) else 1.0
    bench_cagr = ((bench["equity"].iloc[-1] / bench["equity"].iloc[0]) ** (1 / bench_years) - 1) if len(bench) else 0

    # Information ratio: (strategy - benchmark) daily excess return annualized
    bench_ret_d = bench.set_index("date")["equity"].pct_change()
    eq_ret_d = eq.set_index("date")["equity"].pct_change()
    excess = (eq_ret_d - bench_ret_d).dropna()
    if len(excess) > 1 and excess.std() > 0:
        info_ratio = excess.mean() / excess.std() * np.sqrt(252)
    else:
        info_ratio = 0.0

    # Trailing 12-month CAGR (is edge stable, or dying?)
    cutoff_1y = eq["date"].max() - pd.Timedelta(days=365)
    eq_1y = eq[eq["date"] >= cutoff_1y]
    if len(eq_1y) > 20:
        cagr_1y = eq_1y["equity"].iloc[-1] / eq_1y["equity"].iloc[0] - 1
    else:
        cagr_1y = float("nan")

    # Monthly returns distribution
    monthly = eq.set_index("date")["equity"].resample("M").last().pct_change().dropna()
    pct_pos_months = float((monthly > 0).mean()) if len(monthly) else 0.0
    worst_month = float(monthly.min()) if len(monthly) else 0.0
    best_month = float(monthly.max()) if len(monthly) else 0.0

    # Trade stats
    n_trades = len(trades)
    wins = [t for t in trades if t.pnl_pkr > 0]
    losses = [t for t in trades if t.pnl_pkr <= 0]
    win_rate = len(wins) / n_trades if n_trades else 0
    avg_win = np.mean([t.return_pct for t in wins]) if wins else 0
    avg_loss = np.mean([t.return_pct for t in losses]) if losses else 0
    avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0
    profit_factor = (sum(t.pnl_pkr for t in wins) / abs(sum(t.pnl_pkr for t in losses))) if losses else float("inf")
    avg_trade_ret = np.mean([t.return_pct for t in trades]) if trades else 0

    # Cost-sensitivity: what if costs were 2x / 3x? (approximated via trade returns)
    def _cagr_at_cost_mult(mult: float) -> float:
        if not trades:
            return 0.0
        extra_cost = (mult - 1.0) * 2 * (cfg.commission_bps + cfg.slippage_bps) / 10_000
        rets = [t.return_pct - extra_cost for t in trades]
        # Roughly compound average trade return ^ trades_per_year
        trades_per_year = n_trades / max(years, 1e-9)
        r_mean = float(np.mean(rets))
        return (1 + r_mean) ** trades_per_year - 1

    return {
        "days": days,
        "years": round(years, 2),
        "initial_capital": cfg.initial_capital,
        "final_equity": round(final, 0),
        "total_return": round(total_ret, 4),
        "cagr": round(cagr, 4),
        "cagr_1y": round(cagr_1y, 4) if not (isinstance(cagr_1y, float) and np.isnan(cagr_1y)) else None,
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3) if np.isfinite(calmar) else 99.0,
        "info_ratio": round(info_ratio, 3),
        "max_drawdown": round(max_dd, 4),
        "pct_positive_months": round(pct_pos_months, 3),
        "worst_month": round(worst_month, 4),
        "best_month": round(best_month, 4),
        "bench_total_return": round(bench_ret, 4),
        "bench_cagr": round(bench_cagr, 4),
        "alpha_vs_bench": round(total_ret - bench_ret, 4),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 3),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "avg_trade_pct": round(avg_trade_ret, 4),
        "profit_factor": round(profit_factor, 2),
        "avg_hold_days": round(avg_hold, 1),
        "cagr_at_1x_cost": round(_cagr_at_cost_mult(1.0), 4),
        "cagr_at_2x_cost": round(_cagr_at_cost_mult(2.0), 4),
        "cagr_at_3x_cost": round(_cagr_at_cost_mult(3.0), 4),
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def write_report(result: BacktestResult, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (PROJECT_ROOT / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    md_path = out_dir / f"backtest_{ts}.md"
    eq_path = out_dir / f"backtest_{ts}_equity.csv"
    tr_path = out_dir / f"backtest_{ts}_trades.csv"

    result.equity_curve.to_csv(eq_path, index=False)
    pd.DataFrame([t.__dict__ for t in result.trades]).to_csv(tr_path, index=False)

    s = result.stats
    cfg = result.config

    # Per-symbol trade summary (manual markdown to avoid tabulate dep)
    per_sym_md = "(no trades)"
    if result.trades:
        td = pd.DataFrame([t.__dict__ for t in result.trades])
        per_sym = td.groupby("symbol").agg(
            n=("pnl_pkr", "count"),
            wins=("pnl_pkr", lambda x: (x > 0).sum()),
            total_pnl=("pnl_pkr", "sum"),
            avg_ret=("return_pct", "mean"),
        ).sort_values("total_pnl", ascending=False)
        rows = ["| Symbol | Trades | Wins | Total PnL (PKR) | Avg Return |",
                "|---|---:|---:|---:|---:|"]
        for sym, r in per_sym.iterrows():
            rows.append(
                f"| {sym} | {int(r['n'])} | {int(r['wins'])} | "
                f"{r['total_pnl']:,.0f} | {r['avg_ret']:+.2%} |"
            )
        per_sym_md = "\n".join(rows)

    # Economic gate table
    econ_md = "(no economics computed)"
    if not result.economics.empty:
        rows = ["| Symbol | n_trades | Avg ret/trade | Trade Sharpe | PF | Viable? |",
                "|---|---:|---:|---:|---:|:---:|"]
        for _, r in result.economics.iterrows():
            flag = "✓" if r["economically_viable"] else "✗"
            rows.append(
                f"| {r['symbol']} | {int(r['n_trades'])} | {r['avg_return']:+.2%} | "
                f"{r['sharpe_trade']} | {r['profit_factor']} | {flag} |"
            )
        econ_md = "\n".join(rows)
        threshold_pct = result.economics["threshold"].iloc[0]
        econ_md = (
            f"_Gate: avg net return per trade must clear "
            f"**{threshold_pct:+.2%}** (= {cfg.econ_gate_mult_of_cost}x round-trip cost)._\n\n"
            + econ_md
        )

    blocked_md = "none"
    if result.blocked_by_gate:
        blocked_md = ", ".join(result.blocked_by_gate)

    lines = [
        f"# Backtest Report ({ts})",
        "",
        "## Config",
        f"- Entry threshold: {cfg.entry_threshold}    Exit threshold: {cfg.exit_threshold}",
        f"- Trailing stop: {cfg.stop_pct:.0%}    Max hold: {cfg.max_hold_days}d",
        f"- Max positions: {cfg.max_positions}    Max per sector: {cfg.max_per_sector}",
        f"- Round-trip cost: {(cfg.commission_bps + cfg.slippage_bps) * 2 / 100:.2f}%",
        f"- Sizing: {'conviction-weighted' if cfg.conviction_sizing else 'equal-weight'}"
        + (f" (base={cfg.base_pos_pct:.0%}, range=[{cfg.min_pos_pct:.0%}, {cfg.max_pos_pct:.0%}])"
           if cfg.conviction_sizing else ""),
        f"- Economic gate: {'ON' if cfg.apply_economic_gate else 'OFF'}"
        + (f" (require avg/trade ≥ {cfg.econ_gate_mult_of_cost}x cost)"
           if cfg.apply_economic_gate else ""),
        f"- Blocked by gate: **{blocked_md}**",
        "",
        "## Headline",
        f"- Period: {result.equity_curve['date'].min().date()} to {result.equity_curve['date'].max().date()} ({s['years']} years)",
        f"- **Strategy CAGR: {s['cagr']:+.2%}**    |  **Total return: {s['total_return']:+.2%}**",
        f"- Trailing 1y: {s['cagr_1y']:+.2%}" if s['cagr_1y'] is not None else "- Trailing 1y: n/a",
        f"- **Benchmark (equal-weight) CAGR: {s['bench_cagr']:+.2%}**    |  **Alpha: {s['alpha_vs_bench']:+.2%}**",
        "",
        "## Risk-adjusted",
        f"- Sharpe: {s['sharpe']}    Sortino: {s['sortino']}",
        f"- **Calmar (CAGR / |MaxDD|): {s['calmar']}**",
        f"- Information ratio vs benchmark: {s['info_ratio']}",
        f"- Max drawdown: {s['max_drawdown']:.2%}",
        f"- Positive months: {s['pct_positive_months']:.1%}    Worst/Best: {s['worst_month']:+.2%} / {s['best_month']:+.2%}",
        "",
        "## Trades",
        f"- Total: {s['n_trades']}    Win rate: {s['win_rate']:.1%}    Profit factor: {s['profit_factor']}",
        f"- Avg trade: {s['avg_trade_pct']:+.2%}    Avg winner: {s['avg_win_pct']:+.2%}    Avg loser: {s['avg_loss_pct']:+.2%}",
        f"- Avg hold: {s['avg_hold_days']}d",
        "",
        "## Cost sensitivity (breakeven check)",
        f"- Approx CAGR at 1x costs ({(cfg.commission_bps + cfg.slippage_bps) * 2 / 100:.2f}%): {s['cagr_at_1x_cost']:+.2%}",
        f"- Approx CAGR at 2x costs ({(cfg.commission_bps + cfg.slippage_bps) * 4 / 100:.2f}%): {s['cagr_at_2x_cost']:+.2%}",
        f"- Approx CAGR at 3x costs ({(cfg.commission_bps + cfg.slippage_bps) * 6 / 100:.2f}%): {s['cagr_at_3x_cost']:+.2%}",
        "",
        "## Per-symbol walk-forward economics (drives the gate)",
        "",
        econ_md,
        "",
        "## Per-symbol realized P&L",
        "",
        per_sym_md,
        "",
        "## Files",
        f"- Equity curve: `{eq_path.name}`",
        f"- Trade log: `{tr_path.name}`",
        f"- Economics: `models/economic_gate.json`",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


if __name__ == "__main__":
    from rich.console import Console
    from rich.table import Table
    console = Console()
    console.rule("[bold cyan]Running walk-forward backtest")
    result = run_backtest()
    path = write_report(result)
    s = result.stats

    table = Table(title="Backtest Summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Period (years)", str(s["years"]))
    table.add_row("Strategy CAGR", f"{s['cagr']:+.2%}")
    table.add_row("  trailing 1y", f"{s['cagr_1y']:+.2%}" if s["cagr_1y"] is not None else "n/a")
    table.add_row("Benchmark CAGR", f"{s['bench_cagr']:+.2%}")
    table.add_row("Alpha vs benchmark", f"{s['alpha_vs_bench']:+.2%}")
    table.add_row("Sharpe", f"{s['sharpe']}")
    table.add_row("Sortino", f"{s['sortino']}")
    table.add_row("Calmar", f"{s['calmar']}")
    table.add_row("Info ratio", f"{s['info_ratio']}")
    table.add_row("Max drawdown", f"{s['max_drawdown']:.2%}")
    table.add_row("Trades", str(s["n_trades"]))
    table.add_row("Win rate", f"{s['win_rate']:.1%}")
    table.add_row("Profit factor", f"{s['profit_factor']}")
    table.add_row("Avg hold days", str(s["avg_hold_days"]))
    if result.blocked_by_gate:
        table.add_row("Blocked by gate", ", ".join(result.blocked_by_gate))
    console.print(table)
    console.print(f"\n[green]Full report:[/green] {path}")
