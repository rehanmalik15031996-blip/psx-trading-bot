"""Gap-3 deep-dive on brent_plateau_e_and_p_decay alone.

Brent has 5+ years of parquet history. So unlike the intraday-KSE
cases, we CAN do a real OOS test of this case.

For every PSX trading Friday from 2021-06 to 2026-04 (pre-Phase F):
  - compute brent_5d_slope and brent level
  - check if brent_plateau case fires (slope<=1.0 AND level>=100)
  - check the forward 5d return of the OGDC/PPL/POL E&P basket (since
    that's the sector the case is supposed to predict will UNDERPERFORM)
  - check forward 5d return of KSE-100 universe (so we can compute
    relative under/outperformance)

A valid case should:
  1. Not fire on >25-30% of days
  2. When it fires, the E&P basket should underperform the universe
     (forward 5d alpha should be NEGATIVE)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OHLCV = ROOT / "data" / "ohlcv"
MACRO = ROOT / "data" / "macro"

E_AND_P = ["OGDC", "PPL", "POL"]


def _load_brent() -> pd.DataFrame:
    df = pd.read_parquet(MACRO / "brent.parquet")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _load_ohlcv(sym: str) -> pd.DataFrame:
    p = OHLCV / f"{sym}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _fwd_5d(df: pd.DataFrame, d: date) -> float | None:
    """Return 5-trading-day forward return on `close`."""
    after = df[df["date"] > d]
    if len(after) < 5:
        return None
    t0_row = df[df["date"] == d]
    if t0_row.empty:
        return None
    close_col = "close" if "close" in df.columns else "kse100_close"
    t0 = float(t0_row.iloc[0][close_col])
    t5 = float(after.iloc[4][close_col])
    return (t5 - t0) / t0 * 100


def _brent_slope(brent: pd.DataFrame, d: date) -> tuple[float | None, float | None]:
    sub = brent[brent["date"] <= d].tail(6)
    if len(sub) < 6:
        return None, None
    close_col = "close" if "close" in brent.columns else "value"
    if close_col not in brent.columns:
        # try common alt names
        for c in ["brent_close", "px_close", "close"]:
            if c in brent.columns:
                close_col = c
                break
        else:
            return None, None
    a = float(sub.iloc[0][close_col])
    z = float(sub.iloc[-1][close_col])
    if a == 0:
        return None, None
    return (z - a) / a * 100, z


def main() -> int:
    brent = _load_brent()
    print(f"Brent rows: {len(brent)}, columns: {list(brent.columns)}")
    universe = {sym: _load_ohlcv(sym) for sym in E_AND_P}
    universe = {k: v for k, v in universe.items() if not v.empty}
    print(f"E&P symbols loaded: {list(universe.keys())}")
    other_syms = ["HUBC","MLCF","FCCL","HBL","MEBL","MCB","UBL","LUCK","DGKC"]
    other = {sym: _load_ohlcv(sym) for sym in other_syms}
    other = {k: v for k, v in other.items() if not v.empty}
    print(f"Non-E&P comparison symbols: {list(other.keys())}")

    # Walk weekly from 2021-06 to 2026-04 (pre-Phase F)
    start = date(2021, 6, 4)
    end   = date(2026, 4, 30)
    dates: list[date] = []
    d = start
    while d <= end:
        if d.weekday() == 4:   # Friday
            dates.append(d)
        d += timedelta(days=1)
    print(f"Windows: {len(dates)} Fridays")

    fires: list[dict] = []
    n_evaluated = 0
    for d in dates:
        slope, lvl = _brent_slope(brent, d)
        if slope is None or lvl is None:
            continue
        n_evaluated += 1
        if not (slope <= 1.0 and lvl >= 100.0):
            continue
        # Case fires — measure forward 5d E&P avg vs comparison avg
        ep_rets = []
        for sym, df in universe.items():
            r = _fwd_5d(df, d)
            if r is not None:
                ep_rets.append(r)
        other_rets = []
        for sym, df in other.items():
            r = _fwd_5d(df, d)
            if r is not None:
                other_rets.append(r)
        if not ep_rets or not other_rets:
            continue
        ep_mean    = sum(ep_rets) / len(ep_rets)
        other_mean = sum(other_rets) / len(other_rets)
        alpha = ep_mean - other_mean   # E&P alpha vs the rest
        fires.append({
            "date": d.isoformat(),
            "brent_slope_5d": round(slope, 2),
            "brent_level": round(lvl, 2),
            "ep_fwd5d_pct":    round(ep_mean, 3),
            "other_fwd5d_pct": round(other_mean, 3),
            "ep_alpha_pp":     round(alpha, 3),
        })

    print()
    print(f"Total Fridays evaluated:    {n_evaluated}")
    print(f"Times case fired:           {len(fires)} "
          f"({len(fires)/max(n_evaluated,1)*100:.1f}% of days)")
    if not fires:
        print("Case never fired — threshold may be too tight for "
              "pre-2024 Brent regime.")
        return 0

    n_neg_alpha = sum(1 for f in fires if f["ep_alpha_pp"] < 0)
    mean_alpha  = sum(f["ep_alpha_pp"] for f in fires) / len(fires)
    print()
    print(f"E&P alpha vs other sectors on fire days:")
    print(f"  mean alpha:          {mean_alpha:+.3f}pp")
    print(f"  fires with neg alpha (case CORRECT): "
          f"{n_neg_alpha}/{len(fires)} = {n_neg_alpha/len(fires)*100:.1f}%")

    # Bucket by decade-style sub-windows to check stability
    bins = {"2021-22": [], "2023-24": [], "2025-04/26": []}
    for f in fires:
        y = int(f["date"].split("-")[0])
        m = int(f["date"].split("-")[1])
        if y <= 2022:
            bins["2021-22"].append(f)
        elif y <= 2024:
            bins["2023-24"].append(f)
        else:
            bins["2025-04/26"].append(f)

    print()
    print("Sub-window stability:")
    for k, fs in bins.items():
        if not fs:
            print(f"  {k}: no fires")
            continue
        a = sum(f["ep_alpha_pp"] for f in fs) / len(fs)
        n_neg = sum(1 for f in fs if f["ep_alpha_pp"] < 0)
        print(f"  {k}: n={len(fs):>3}  mean_alpha={a:+.3f}pp  "
              f"neg_alpha_rate={n_neg/len(fs)*100:.0f}%")

    print()
    print("=" * 88)
    print("VERDICT")
    print("=" * 88)
    fire_rate = len(fires) / max(n_evaluated, 1)
    if fire_rate > 0.30:
        print(f"  [TOO LOOSE] case fires on {fire_rate*100:.0f}% of "
              "trading days — threshold needs tightening.")
    if abs(mean_alpha) < 0.20:
        print(f"  [NO EDGE] mean E&P alpha on fire days is "
              f"{mean_alpha:+.2f}pp — case does not predict E&P "
              "underperformance. RECOMMEND: REMOVE or recalibrate to "
              "harder triggers.")
    elif mean_alpha < -0.30 and n_neg_alpha / len(fires) > 0.55:
        print(f"  [VALID] case correctly predicts E&P underperformance "
              f"({mean_alpha:+.2f}pp alpha, {n_neg_alpha/len(fires)*100:.0f}% "
              "hit rate). Keep.")
    else:
        print(f"  [WEAK] mean alpha {mean_alpha:+.2f}pp, hit "
              f"{n_neg_alpha/len(fires)*100:.0f}% — borderline.")

    out = ROOT / "data" / "_research" / "brent_plateau_oos.json"
    out.write_text(json.dumps({"n_evaluated": n_evaluated,
                                 "fires": fires}, indent=2),
                    encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
