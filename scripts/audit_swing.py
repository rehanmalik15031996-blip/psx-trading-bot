"""Plan E: catalyst-gated swing trading backtester.

This is the **honest feasibility test** for the swing layer. We simulate the
mechanical core of Plan E bar-by-bar on the 5-year history and compare to
Phase 1. News is NOT included here — we test whether the technical skeleton
has an edge before bolting on LLM/news. If this fails, news won't save it.

Strategy rules (no look-ahead):
  At close of day t we observe:
    1. Weekly watchlist (refreshed every Friday close): top-N by 150d momentum,
       vol<70pct, market filter ON (same math as Phase 1 but larger N).
    2. For each held position, any exit trigger → signal SELL at open(t+1).
    3. If under capacity, for each watchlist name not held, check entry
       trigger → signal BUY at open(t+1).

Entry trigger (at close of day t):
    (a) close_t > max(close_{t-20..t-1})        (20d breakout)
    (b) volume_t > 1.2 * mean(volume_{t-20..t-1})  (volume confirmation)

Exit triggers (any one of):
    (a) trailing stop: close_t <= peak_px * (1 - stop_pct)
    (b) close_t < min(close_{t-20..t-1})       (20d breakdown)
    (c) symbol not in the last 2 weekly watchlists (signal decay)
    (d) held > max_hold_days with no new 20d high (time stop)

Execution: fills at open(t+1) — conservative. If open not available, fallback
to close(t+1). Costs: 20 bps per fill (40 bps round-trip).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
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
    StrategyConfig, compute_momentum, compute_realized_vol, build_prices_wide,
)
from config.universe import symbols as universe_symbols
from data.store import load_ohlcv


REPORT_DIR = PROJECT_ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)


@dataclass
class SwingConfig:
    # Watchlist (Friday refresh) -------------------------------------
    momentum_window: int = 150
    vol_window: int = 20
    vol_rank_cap: float = 0.70
    watchlist_n: int = 8              # top-8 candidates
    market_filter_on: bool = True
    market_mom_window: int = 150

    # Entry gate (at close of day t, act on open t+1) -----------------
    breakout_window: int = 20         # close > 20d high
    volume_confirm_mult: float = 1.2  # vol > 1.2× 20d mean
    require_volume_confirm: bool = True

    # Exit rules ------------------------------------------------------
    trailing_stop_pct: float = 0.12
    breakdown_window: int = 20        # close < 20d low
    decay_weeks: int = 2              # off watchlist for 2 wks -> exit
    max_hold_days: int = 60           # time stop if no new 20d high

    # Position sizing -------------------------------------------------
    max_positions: int = 5
    cost_round_trip: float = 0.004    # 40 bps, 20 bps/side


def _fridays(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    return {d for d in index if d.weekday() == 4}


def _load_ohlcv_wide(symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    closes, opens, volumes = {}, {}, {}
    for s in symbols:
        d = load_ohlcv(s)
        if d.empty:
            continue
        d = d.sort_values("date").set_index("date")
        closes[s] = d["close"]
        opens[s]  = d["open"] if "open" in d.columns else d["close"]
        volumes[s] = d["volume"] if "volume" in d.columns else pd.Series(
            np.ones(len(d)), index=d.index)
    c = pd.DataFrame(closes).ffill()
    o = pd.DataFrame(opens).reindex(c.index).ffill()
    v = pd.DataFrame(volumes).reindex(c.index).fillna(0)
    c.index = pd.to_datetime(c.index)
    o.index = pd.to_datetime(c.index)
    v.index = pd.to_datetime(c.index)
    return c, o, v


def _build_watchlists(
    close: pd.DataFrame, cfg: SwingConfig,
) -> dict[pd.Timestamp, list[str]]:
    """Pre-compute the watchlist at each Friday close."""
    mom = compute_momentum(close, cfg.momentum_window)
    vol = compute_realized_vol(close, cfg.vol_window)
    fridays = sorted(_fridays(close.index))
    wl: dict[pd.Timestamp, list[str]] = {}
    for dt in fridays:
        if dt not in mom.index:
            continue
        mom_row = mom.loc[dt].dropna()
        vol_row = vol.loc[dt]
        if mom_row.empty:
            continue
        if cfg.market_filter_on and mom_row.mean() < 0:
            wl[dt] = []
            continue
        vr = vol_row.loc[mom_row.index].rank(pct=True)
        keep = vr[vr <= cfg.vol_rank_cap].index
        cand = mom_row[mom_row.index.intersection(keep)]
        wl[dt] = cand.sort_values(ascending=False).head(cfg.watchlist_n).index.tolist()
    return wl


@dataclass
class SwingTrade:
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
class SwingResult:
    cfg: dict
    equity: pd.Series
    benchmark: pd.Series
    daily_returns: pd.Series
    bench_returns: pd.Series
    trades: list[SwingTrade]
    actions_log: list[dict]
    metrics: dict


def simulate_swing(cfg: SwingConfig | None = None) -> SwingResult:
    cfg = cfg or SwingConfig()
    syms = universe_symbols()
    close, open_, volume = _load_ohlcv_wide(syms)
    close = close.dropna(how="all")
    open_  = open_.reindex(close.index).ffill()
    volume = volume.reindex(close.index).fillna(0)

    dates = close.index
    hi20 = close.shift(1).rolling(cfg.breakout_window).max()
    lo20 = close.shift(1).rolling(cfg.breakdown_window).min()
    vol_mean20 = volume.shift(1).rolling(cfg.breakout_window).mean()

    watchlists = _build_watchlists(close, cfg)
    wl_fridays = sorted(watchlists.keys())

    def last_two_watchlists(dt: pd.Timestamp) -> list[list[str]]:
        recent = [f for f in wl_fridays if f <= dt]
        return [watchlists[f] for f in recent[-cfg.decay_weeks:]]

    # --- state ---
    cash = 1.0
    positions: dict[str, dict] = {}
    equity_path: list[float] = []
    pending_buys: list[str] = []
    pending_sells: list[str] = []
    trades: list[SwingTrade] = []
    actions_log: list[dict] = []

    for i, dt in enumerate(dates):
        prev_close = close.iloc[i - 1] if i > 0 else close.iloc[i]

        # 1. Execute pending orders at today's OPEN
        todays_open = open_.iloc[i]
        for sym in list(pending_sells):
            if sym not in positions:
                continue
            px = todays_open[sym]
            if not pd.notna(px):
                px = close.iloc[i][sym]
            pos = positions.pop(sym)
            gross = pos["shares"] * px
            cash += gross * (1 - cfg.cost_round_trip / 2)
            ret = px / pos["entry_px"] - 1
            trades.append(SwingTrade(
                symbol=sym, entry_date=pos["entry_date"], exit_date=str(dt.date()),
                entry_px=pos["entry_px"], exit_px=float(px),
                peak_px=pos["peak_px"], hold_days=pos["hold_days"],
                ret_pct=float(ret), exit_reason=pos["exit_reason"]))
            actions_log.append({"date": str(dt.date()), "action": "SELL",
                                "symbol": sym, "px": float(px),
                                "reason": pos["exit_reason"],
                                "ret_pct": float(ret)})
        pending_sells = []

        for sym in list(pending_buys):
            if sym in positions or len(positions) >= cfg.max_positions:
                continue
            px = todays_open[sym]
            if not pd.notna(px):
                px = close.iloc[i][sym]
            open_slots = cfg.max_positions - len(positions) - (len(pending_buys) - 1)
            alloc = cash / max(open_slots, 1) if len(positions) < cfg.max_positions else 0
            if alloc <= 0:
                continue
            shares = alloc / px / (1 + cfg.cost_round_trip / 2)
            cost = shares * px * (1 + cfg.cost_round_trip / 2)
            cash -= cost
            positions[sym] = {
                "entry_date": str(dt.date()),
                "entry_px": float(px),
                "peak_px": float(px),
                "shares": float(shares),
                "hold_days": 0,
                "last_new_high_day": 0,
                "exit_reason": "",
            }
            actions_log.append({"date": str(dt.date()), "action": "BUY",
                                "symbol": sym, "px": float(px),
                                "reason": "breakout + volume"})
        pending_buys = []

        # 2. Update peaks and age
        today_close = close.iloc[i]
        for sym in positions:
            px = today_close[sym]
            if pd.notna(px):
                if px > positions[sym]["peak_px"]:
                    positions[sym]["peak_px"] = float(px)
                    positions[sym]["last_new_high_day"] = 0
                else:
                    positions[sym]["last_new_high_day"] += 1
            positions[sym]["hold_days"] += 1

        # 3. Mark-to-market equity
        pos_val = sum(positions[s]["shares"] * today_close[s]
                      for s in positions if pd.notna(today_close[s]))
        equity_path.append(cash + pos_val)

        # 4. Decide exits (execute at open(t+1))
        current_watchlist = watchlists.get(max((f for f in wl_fridays if f <= dt),
                                               default=pd.NaT), [])
        last_k_wl = last_two_watchlists(dt)
        still_on_any = set().union(*last_k_wl) if last_k_wl else set()

        for sym, pos in positions.items():
            if pos["exit_reason"]:
                continue
            px = today_close[sym]
            if not pd.notna(px):
                continue
            # trailing stop
            if px <= pos["peak_px"] * (1 - cfg.trailing_stop_pct):
                pos["exit_reason"] = f"trailing stop -{int(cfg.trailing_stop_pct*100)}%"
            # breakdown
            elif pd.notna(lo20.iloc[i][sym]) and px < lo20.iloc[i][sym]:
                pos["exit_reason"] = f"20d breakdown"
            # time stop (no new high for max_hold_days)
            elif pos["last_new_high_day"] >= cfg.max_hold_days:
                pos["exit_reason"] = f"time stop {cfg.max_hold_days}d no new high"
            # decay (off watchlist for decay_weeks)
            elif (len(last_k_wl) >= cfg.decay_weeks
                  and sym not in still_on_any):
                pos["exit_reason"] = f"signal decay ({cfg.decay_weeks}w off watchlist)"
        for sym, pos in positions.items():
            if pos["exit_reason"]:
                pending_sells.append(sym)

        # 5. Decide entries (only if capacity after pending exits will clear)
        projected_positions = len(positions) - len(pending_sells)
        if projected_positions < cfg.max_positions and current_watchlist:
            cap = cfg.max_positions - projected_positions
            for sym in current_watchlist:
                if cap <= 0:
                    break
                if sym in positions or sym in pending_buys:
                    continue
                px = today_close[sym]
                hi = hi20.iloc[i][sym]
                vm = vol_mean20.iloc[i][sym]
                vt = volume.iloc[i][sym]
                if not (pd.notna(px) and pd.notna(hi)):
                    continue
                if px <= hi:
                    continue
                if cfg.require_volume_confirm:
                    if not pd.notna(vm) or vm <= 0 or vt < cfg.volume_confirm_mult * vm:
                        continue
                pending_buys.append(sym)
                cap -= 1

    equity = pd.Series(equity_path, index=dates)
    r = close.pct_change().fillna(0)
    daily_ret = equity.pct_change().fillna(0)
    # B&H benchmark = equal-weighted universe
    bench_w = pd.DataFrame(1.0 / len(r.columns), index=r.index, columns=r.columns)
    bench_ret = (bench_w.shift(1) * r).sum(axis=1).fillna(0)
    bench_eq = (1 + bench_ret).cumprod()

    metrics = _metrics(daily_ret, bench_ret, equity, bench_eq)
    return SwingResult(
        cfg=cfg.__dict__, equity=equity, benchmark=bench_eq,
        daily_returns=daily_ret, bench_returns=bench_ret,
        trades=trades, actions_log=actions_log, metrics=metrics,
    )


def _metrics(r, br, eq, beq) -> dict:
    r = r.dropna(); br = br.dropna()
    n = len(r); y = n / 252
    cagr = eq.iloc[-1] ** (1 / y) - 1 if y > 0 else 0
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    calmar = cagr / abs(dd) if dd < 0 else float("inf")
    dr = r[r < 0]
    sortino = r.mean() / dr.std() * np.sqrt(252) if len(dr) and dr.std() > 0 else 0
    b_cagr = beq.iloc[-1] ** (1 / y) - 1 if y > 0 else 0
    b_sh = br.mean() / br.std() * np.sqrt(252) if br.std() > 0 else 0
    b_dd = (beq / beq.cummax() - 1).min()
    return {"cagr": cagr, "sharpe": sharpe, "sortino": sortino,
            "max_drawdown": dd, "calmar": calmar,
            "bench_cagr": b_cagr, "bench_sharpe": b_sh, "bench_dd": b_dd,
            "alpha_cagr": cagr - b_cagr, "n_days": n}


def _print_result(title: str, res: SwingResult, console: Console) -> None:
    m = res.metrics
    t = Table(title=title)
    t.add_column("Metric"); t.add_column("Strategy", justify="right"); t.add_column("B&H", justify="right")
    t.add_row("CAGR", f"{m['cagr']:+.2%}", f"{m['bench_cagr']:+.2%}")
    t.add_row("Sharpe", f"{m['sharpe']:.2f}", f"{m['bench_sharpe']:.2f}")
    t.add_row("Sortino", f"{m['sortino']:.2f}", "—")
    t.add_row("Max DD", f"{m['max_drawdown']:+.2%}", f"{m['bench_dd']:+.2%}")
    t.add_row("Calmar", f"{m['calmar']:.2f}", "—")
    t.add_row("N trades", str(len(res.trades)), "—")
    if res.trades:
        wins = [tr.ret_pct for tr in res.trades if tr.ret_pct > 0]
        t.add_row("Win rate", f"{len(wins)/len(res.trades)*100:.1f}%", "—")
        t.add_row("Avg ret/trade", f"{np.mean([tr.ret_pct for tr in res.trades]):+.2%}", "—")
        t.add_row("Avg hold (days)", f"{np.mean([tr.hold_days for tr in res.trades]):.1f}", "—")
    console.print(t)


def main() -> int:
    console = Console()
    console.rule("[bold cyan]Plan E audit — catalyst-gated swing strategy")

    configs = {
        "Strict  (breakout+vol, 5 pos, 12% stop)":
            SwingConfig(),
        "Medium  (breakout only, 5 pos, 12% stop)":
            SwingConfig(require_volume_confirm=False),
        "Wide    (7 pos, 15% stop, 90d time stop)":
            SwingConfig(max_positions=7, trailing_stop_pct=0.15, max_hold_days=90),
        "Tight   (3 pos, 10% stop, breakout+vol)":
            SwingConfig(max_positions=3, trailing_stop_pct=0.10),
        "FastFilter (60d market filter, breakout+vol)":
            SwingConfig(market_mom_window=60),
        "NoFilter (no market filter at all)":
            SwingConfig(market_filter_on=False, max_positions=5),
    }

    results = {}
    for name, cfg in configs.items():
        console.print(f"\nRunning [yellow]{name}[/yellow] ...")
        res = simulate_swing(cfg)
        results[name] = res
        _print_result(name, res, console)

    # Comparison table
    console.rule("[bold]Summary (all variants)")
    cmp_t = Table()
    cmp_t.add_column("Variant")
    cmp_t.add_column("CAGR", justify="right")
    cmp_t.add_column("Sharpe", justify="right")
    cmp_t.add_column("MaxDD", justify="right")
    cmp_t.add_column("Calmar", justify="right")
    cmp_t.add_column("Trades", justify="right")
    cmp_t.add_column("Win%", justify="right")

    for name, res in results.items():
        m = res.metrics
        wr = (sum(1 for tr in res.trades if tr.ret_pct > 0) / len(res.trades) * 100
              if res.trades else 0)
        cmp_t.add_row(name, f"{m['cagr']:+.2%}", f"{m['sharpe']:.2f}",
                      f"{m['max_drawdown']:+.2%}", f"{m['calmar']:.2f}",
                      str(len(res.trades)), f"{wr:.0f}%")

    b = next(iter(results.values()))
    cmp_t.add_row("[bold]Buy & hold[/bold]",
                  f"{b.metrics['bench_cagr']:+.2%}",
                  f"{b.metrics['bench_sharpe']:.2f}",
                  f"{b.metrics['bench_dd']:+.2%}", "—", "—", "—")
    console.print(cmp_t)

    # Reference Phase 1 numbers (from psx_strategy_v2.md)
    console.print("\n[bold]Reference: Phase 1[/bold] (monthly top-5, market filter):")
    console.print("  CAGR +18.2% · Sharpe 0.92 · Max DD -21.4% · Calmar 0.85")
    console.print("  See reports/backtest_v2_core.md for full numbers.")

    # April 2026 specific check — did any variant catch the bounce?
    console.rule("[bold cyan]April 2026 slice: which variant caught the rally?")
    apr_t = Table()
    apr_t.add_column("Variant")
    apr_t.add_column("April ret", justify="right")
    apr_t.add_column("# buys in Apr", justify="right")
    apr_t.add_column("# sells in Apr", justify="right")
    apr_t.add_column("Held @ Apr end", justify="left")
    apr_start = pd.Timestamp("2026-04-01")
    apr_end = pd.Timestamp("2026-04-30")
    for name, res in results.items():
        eq = res.equity
        apr_dates = eq.index[(eq.index >= apr_start) & (eq.index <= apr_end)]
        if len(apr_dates) == 0:
            continue
        pre_apr = eq.index[eq.index < apr_start]
        e0 = float(eq.loc[pre_apr[-1]]) if len(pre_apr) else float(eq.iloc[0])
        e1 = float(eq.loc[apr_dates[-1]])
        ret = e1 / e0 - 1
        buys = [a for a in res.actions_log
                if a["action"] == "BUY" and apr_start.date().isoformat() <= a["date"] <= apr_end.date().isoformat()]
        sells = [a for a in res.actions_log
                 if a["action"] == "SELL" and apr_start.date().isoformat() <= a["date"] <= apr_end.date().isoformat()]
        held_names = set()
        for a in res.actions_log:
            if a["date"] > apr_dates[-1].date().isoformat():
                break
            if a["action"] == "BUY":
                held_names.add(a["symbol"])
            elif a["action"] == "SELL":
                held_names.discard(a["symbol"])
        apr_t.add_row(name, f"{ret:+.2%}", str(len(buys)), str(len(sells)),
                      ", ".join(sorted(held_names)) or "(cash)")
    apr_t.add_row("[bold]Phase 1[/bold]", "-0.20%", "0", "0", "(cash — filter veto)")
    apr_t.add_row("[bold]Buy & hold[/bold]", "+14.03%", "—", "—", "all 15 names")
    console.print(apr_t)

    # Write a markdown summary
    from datetime import datetime
    out = REPORT_DIR / "audit_swing.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Plan E audit — swing strategy ({datetime.now():%Y-%m-%d %H:%M})\n\n")
        f.write("Tested without news overlay (price/volume only).\n\n")
        f.write("| Variant | CAGR | Sharpe | MaxDD | Calmar | Trades | Win% |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|\n")
        for name, res in results.items():
            m = res.metrics
            wr = (sum(1 for tr in res.trades if tr.ret_pct > 0) / len(res.trades) * 100
                  if res.trades else 0)
            f.write(f"| {name} | {m['cagr']:+.2%} | {m['sharpe']:.2f} | "
                    f"{m['max_drawdown']:+.2%} | {m['calmar']:.2f} | "
                    f"{len(res.trades)} | {wr:.0f}% |\n")
        f.write(f"| **Buy & hold** | {b.metrics['bench_cagr']:+.2%} | "
                f"{b.metrics['bench_sharpe']:.2f} | {b.metrics['bench_dd']:+.2%} | "
                f"— | — | — |\n")
        f.write(f"| **Phase 1 (reference)** | +18.18% | 0.92 | -21.44% | 0.85 | 45 | 62% |\n")
    console.print(f"\n[dim]Summary → {out}[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
