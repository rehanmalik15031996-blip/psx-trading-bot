"""Audit the production Phase-1 config on the current 35-stock universe.

The legacy ``audit_low_turnover.py`` covers a grid of monthly / quarterly
rules but never tests the *exact* production setup (top_n=5 with vol +
market filter on). Plan D's strategy.py defaults are:

    top_n=5, momentum_window=150, vol_rank_cap=0.70, market_filter_on=True

This script answers two questions on the post-2026-04-30 35-stock set:

1. What does the live config actually deliver vs B&H today?
2. Does any neighbour (top_n in {3,5,7}, mom in {100,150,200}, vol cap
   in {0.6,0.7,1.0}) beat it?

Outputs metrics (CAGR / Sharpe / Sortino / Calmar / MaxDD) for each row
and writes a JSON summary to ``data/backtest/audit_production_35.json``
so the strategy doc / README can cite live numbers.

Run:
    .venv\\Scripts\\python.exe scripts\\audit_production_config.py
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              line_buffering=True)
sys.path.insert(0, ".")

import numpy as np
import pandas as pd

from config.universe import symbols
from data.store import load_ohlcv

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "audit_production_35.json"

COST_RT = 0.004  # 40 bps round-trip


def _load_wide() -> tuple[pd.DataFrame, pd.DataFrame]:
    closes: dict[str, pd.Series] = {}
    for s in symbols():
        d = load_ohlcv(s)
        if d.empty:
            continue
        d = d.sort_values("date").set_index("date")
        closes[s] = d["close"]
    wide = pd.DataFrame(closes).ffill()
    wide.index = pd.to_datetime(wide.index)
    return wide, np.log(wide).diff()


def _simulate(weights: pd.DataFrame, r: pd.DataFrame,
              cost: float = COST_RT) -> pd.Series:
    w = weights.ffill().fillna(0)
    dw = w.diff().abs().fillna(w.abs())
    daily_cost = dw.sum(axis=1) * (cost / 2)
    gross = (w.shift(1) * r).sum(axis=1)
    return gross - daily_cost.shift(1).fillna(0)


def _metrics(rets: pd.Series) -> dict:
    rets = rets.dropna()
    if len(rets) < 60:
        return {"error": "insufficient data", "n_days": int(len(rets))}
    cum = (1 + rets).cumprod()
    yrs = len(rets) / 252
    cagr = cum.iloc[-1] ** (1 / yrs) - 1
    vol = rets.std() * np.sqrt(252) if rets.std() > 0 else 0.0
    sh = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0.0
    ds = rets[rets < 0]
    so = rets.mean() / ds.std() * np.sqrt(252) if len(ds) and ds.std() > 0 else 0.0
    dd = (cum / cum.cummax() - 1).min()
    cal = cagr / abs(dd) if dd < 0 else 99.0
    return {
        "n_days": int(len(rets)),
        "years": round(float(yrs), 2),
        "cagr_pct": round(float(cagr) * 100, 2),
        "annualized_vol_pct": round(float(vol) * 100, 2),
        "sharpe": round(float(sh), 2),
        "sortino": round(float(so), 2),
        "calmar": round(float(cal), 2),
        "max_drawdown_pct": round(float(dd) * 100, 2),
    }


def monthly_mom(wide: pd.DataFrame, lr: pd.DataFrame,
                mom_window: int, top_n: int,
                vol_cap: float | None = None,
                market_filter: bool = False) -> pd.Series:
    mom = lr.rolling(mom_window).sum()
    vol = lr.rolling(20).std()
    rebal = lr.resample("ME").last().index
    w = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal):
        if dt not in mom.index:
            continue
        if market_filter and mom.loc[dt].mean() < 0:
            continue
        s = mom.loc[dt].copy()
        if vol_cap is not None:
            vr = vol.loc[dt].rank(pct=True)
            s = s.where(vr <= vol_cap)
        s = s.dropna()
        if len(s) < top_n:
            continue
        top = s.nlargest(top_n).index.tolist()
        end = rebal[i + 1] if i + 1 < len(rebal) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            w.loc[mask, sym] = 1.0 / top_n
    r = wide.pct_change()
    return _simulate(w, r)


def buy_and_hold(wide: pd.DataFrame) -> pd.Series:
    r = wide.pct_change()
    w = pd.DataFrame(1.0 / len(wide.columns), index=wide.index,
                     columns=wide.columns)
    return _simulate(w, r)


def main() -> None:
    print(f"[audit_production_config] starting at {datetime.now().isoformat()}")
    wide, lr = _load_wide()
    print(f"[audit_production_config] universe size = {len(wide.columns)}")
    print(f"[audit_production_config] history = {wide.index.min().date()} "
          f"→ {wide.index.max().date()}  ({len(wide)} rows)")

    bh = _metrics(buy_and_hold(wide))
    print(f"\nBuy & hold equal-weight  : "
          f"CAGR {bh['cagr_pct']:+.2f}%  Sh {bh['sharpe']:.2f}  "
          f"DD {bh['max_drawdown_pct']:+.2f}%")

    grid: list[dict] = []
    for tn in (3, 5, 7):
        for win in (100, 150, 200):
            for vc in (0.60, 0.70, 1.00):
                rets = monthly_mom(wide, lr, win, tn, vc, market_filter=True)
                m = _metrics(rets)
                m.update({"top_n": tn, "mom_window": win, "vol_cap": vc,
                          "market_filter": True})
                grid.append(m)
                print(f"top-{tn:>1d}  mom-{win:>3d}  vol-cap {vc:.2f}  "
                      f"mkt-filter ON   "
                      f"CAGR {m['cagr_pct']:+.2f}%  Sh {m['sharpe']:.2f}  "
                      f"DD {m['max_drawdown_pct']:+.2f}%  "
                      f"Cal {m['calmar']:.2f}")

    prod = next(g for g in grid
                if g["top_n"] == 5 and g["mom_window"] == 150
                and g["vol_cap"] == 0.70)

    payload = {
        "as_of": datetime.now(tz=timezone.utc).isoformat(),
        "universe_size": len(wide.columns),
        "history_start": str(wide.index.min().date()),
        "history_end": str(wide.index.max().date()),
        "history_days": int(len(wide)),
        "cost_round_trip_bps": int(COST_RT * 10_000),
        "buy_and_hold": bh,
        "production_config": {
            "top_n": 5, "mom_window": 150, "vol_rank_cap": 0.70,
            "market_filter": True, "trailing_stop": None,
            "metrics": prod,
        },
        "grid_search": grid,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved → {OUT_PATH.relative_to(ROOT)}")
    print(f"\nProduction config (top-5 / 150d / vol<70 / mkt-filter):")
    print(f"  CAGR {prod['cagr_pct']:+.2f}%   Sharpe {prod['sharpe']:.2f}   "
          f"Sortino {prod['sortino']:.2f}   Calmar {prod['calmar']:.2f}   "
          f"MaxDD {prod['max_drawdown_pct']:+.2f}%")


if __name__ == "__main__":
    main()
