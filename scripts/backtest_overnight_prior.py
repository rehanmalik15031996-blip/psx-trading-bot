"""Backtest the rules-based overnight gap prior across the universe.

For every trading day in the target window we:
  1. Load overnight signals as of previous calendar day (the signals a
     trader would have BEFORE PSX opens that day).
  2. Compute the rules-based expected gap (from ui.overnight).
  3. For each stock in the universe, compute the actual overnight gap
     (open/prev_close - 1).
  4. Score direction hit and magnitude error against 3 baselines:
       a. Zero prior   (always predict 0% gap)
       b. Mean prior   (always predict historical mean gap per ticker)
       c. Rules prior  (our overnight-weighted prior, applied uniformly)

We also compute a universe-wide breadth score: what fraction of stocks
gapped in the predicted direction on each day.

This tells us whether the retuned weights generalize or are Apr-23 overfit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np
import pandas as pd

from ui.overnight import gap_bias_from_overnight, load_overnight

UNIVERSE = ["HUBC", "PABC", "MLCF", "OGDC", "FABL", "PPL",
            "POL", "FCCL", "APL", "EPCL", "KOHC", "SEARL",
            "MCB", "MEBL", "PSO"]
START = pd.Timestamp("2025-11-01")
END = pd.Timestamp("2026-04-22")


def trading_days(sym: str) -> pd.Series:
    df = pd.read_parquet(ROOT / "data" / "ohlcv" / f"{sym}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_all() -> dict[str, pd.DataFrame]:
    out = {}
    for sym in UNIVERSE:
        df = trading_days(sym)
        df = df[(df["date"] >= START - pd.Timedelta(days=5))
                & (df["date"] <= END + pd.Timedelta(days=5))]
        df["prev_close"] = df["close"].shift(1)
        df["gap_pct"] = (df["open"] / df["prev_close"] - 1) * 100
        df["day_pct"] = (df["close"] / df["prev_close"] - 1) * 100
        df["intra_pct"] = (df["close"] / df["open"] - 1) * 100
        out[sym] = df.reset_index(drop=True)
    return out


def mean_prior_per_ticker(frames: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Per-ticker historical mean gap using only pre-START data."""
    out = {}
    for sym, df in frames.items():
        hist = df[df["date"] < START]
        if hist.empty:
            out[sym] = 0.0
        else:
            out[sym] = float(hist["gap_pct"].mean() or 0)
    return out


def classify(gap_pct: float, threshold: float = 0.4) -> str:
    if gap_pct > threshold:
        return "GAP_UP"
    if gap_pct < -threshold:
        return "GAP_DOWN"
    return "FLAT"


def score_row(pred: float, actual: float, threshold: float = 0.4) -> dict:
    p_class = classify(pred, threshold)
    a_class = classify(actual, threshold)
    # Direction hit: same class (both UP, both DOWN, both FLAT)
    dir_hit = (p_class == a_class)
    # Loose hit: sign match (or both small)
    sign_hit = (pred * actual > 0) or (abs(pred) < threshold and abs(actual) < threshold)
    err = pred - actual
    return {"pred": pred, "actual": actual,
            "p_class": p_class, "a_class": a_class,
            "dir_hit": dir_hit, "sign_hit": sign_hit,
            "err_pct": err, "abs_err_pct": abs(err)}


def main():
    print(f"Backtest window: {START.date()} .. {END.date()}")
    print(f"Universe: {len(UNIVERSE)} stocks")
    print("-" * 72)

    frames = load_all()
    # Pick any ticker to define the set of trading days in window
    base_days = frames[UNIVERSE[0]][
        (frames[UNIVERSE[0]]["date"] >= START)
        & (frames[UNIVERSE[0]]["date"] <= END)
    ]["date"].tolist()
    print(f"Trading days in window: {len(base_days)}")

    mean_prior = mean_prior_per_ticker(frames)
    print(f"Mean-prior sample (first 5): "
          f"{ {k: round(v, 3) for k, v in list(mean_prior.items())[:5]} }")

    rows_rules = []
    rows_mean = []
    rows_zero = []
    per_day = []
    missing_overnight = 0

    for d in base_days:
        # Use OVERNIGHT cutoff = calendar day BEFORE PSX trading day d
        overnight_cutoff = d - pd.Timedelta(days=1)
        ov = load_overnight(overnight_cutoff)
        if "error" in ov:
            missing_overnight += 1
            continue
        prior = gap_bias_from_overnight(ov)
        pred_gap = prior["expected_gap_pct"]

        day_actuals = []
        day_classes = []
        for sym, df in frames.items():
            day_row = df[df["date"] == d]
            if day_row.empty:
                continue
            a = float(day_row["gap_pct"].iloc[0])
            if np.isnan(a):
                continue
            rows_rules.append(score_row(pred_gap, a))
            rows_mean.append(score_row(mean_prior[sym], a))
            rows_zero.append(score_row(0.0, a))
            day_actuals.append(a)
            day_classes.append(classify(a))

        # per-day breadth tracking
        if day_actuals:
            breadth_up = sum(1 for c in day_classes if c == "GAP_UP")
            breadth_down = sum(1 for c in day_classes if c == "GAP_DOWN")
            breadth_flat = sum(1 for c in day_classes if c == "FLAT")
            majority = max(
                [("GAP_UP", breadth_up), ("GAP_DOWN", breadth_down),
                 ("FLAT", breadth_flat)], key=lambda t: t[1])[0]
            per_day.append({
                "date": d.date().isoformat(),
                "pred_class": prior["bias"],
                "pred_gap_pct": pred_gap,
                "actual_median_gap_pct": round(float(np.median(day_actuals)), 3),
                "breadth_up": breadth_up,
                "breadth_down": breadth_down,
                "breadth_flat": breadth_flat,
                "majority_class": majority,
                "majority_hit": (prior["bias"] == majority),
            })

    if missing_overnight:
        print(f"NOTE: {missing_overnight}/{len(base_days)} days had no overnight data.")

    # ----------------------------------------------------------------------
    def summarize(label: str, rows: list[dict]) -> dict:
        if not rows:
            return {"label": label, "n": 0}
        n = len(rows)
        return {
            "label": label, "n": n,
            "dir_hit_pct":  round(100 * sum(r["dir_hit"] for r in rows) / n, 1),
            "sign_hit_pct": round(100 * sum(r["sign_hit"] for r in rows) / n, 1),
            "mean_err":     round(sum(r["err_pct"] for r in rows) / n, 3),
            "mean_abs_err": round(sum(r["abs_err_pct"] for r in rows) / n, 3),
            "median_abs_err": round(float(np.median([r["abs_err_pct"] for r in rows])), 3),
        }

    print("\n" + "=" * 90)
    print("STOCK-LEVEL SCOREBOARD  (every ticker, every day)")
    print("=" * 90)
    hdr = f"{'strategy':<20s} {'n':>6s} {'3-CLASS HIT':>12s} {'SIGN HIT':>9s} " \
          f"{'mean err':>10s} {'MAE':>7s} {'median |e|':>12s}"
    print(hdr)
    print("-" * 90)
    for label, rows in [("Zero prior", rows_zero),
                         ("Mean prior (per-tkr)", rows_mean),
                         ("Rules prior (overnight)", rows_rules)]:
        s = summarize(label, rows)
        print(f"{s['label']:<20s} {s['n']:>6d} "
              f"{s['dir_hit_pct']:>11.1f}% {s['sign_hit_pct']:>8.1f}% "
              f"{s['mean_err']:>+10.3f} {s['mean_abs_err']:>7.3f} "
              f"{s['median_abs_err']:>12.3f}")

    # ----------------------------------------------------------------------
    # Day-level breadth — did the prior correctly identify the majority gap?
    # ----------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("DAY-LEVEL BREADTH  (did prior match >=50% majority gap direction?)")
    print("=" * 90)
    if per_day:
        dfd = pd.DataFrame(per_day)
        n = len(dfd)
        hit = dfd["majority_hit"].sum()
        print(f"Days scored: {n}")
        print(f"Majority-class hit rate: {hit}/{n} = {hit/n*100:.1f}%")
        # Conditional: only days where prior was non-flat
        non_flat = dfd[dfd["pred_class"] != "FLAT"]
        if len(non_flat):
            h2 = non_flat["majority_hit"].sum()
            print(f"  excl. FLAT days      : {h2}/{len(non_flat)} = "
                  f"{h2/len(non_flat)*100:.1f}%  "
                  f"(n_flat_days={n - len(non_flat)})")
        # Breakdown by predicted class
        print("\nBy predicted class:")
        for cls in ["GAP_UP", "FLAT", "GAP_DOWN"]:
            sub = dfd[dfd["pred_class"] == cls]
            if sub.empty:
                continue
            h = sub["majority_hit"].sum()
            print(f"  {cls:<8s}  pred {len(sub):>3d} days, "
                  f"majority-hit {h}/{len(sub)} = "
                  f"{h/len(sub)*100:>5.1f}%  "
                  f"actual median gap {sub['actual_median_gap_pct'].median():+.2f}%")

    # ----------------------------------------------------------------------
    # Save the per-day table for further analysis
    # ----------------------------------------------------------------------
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    if per_day:
        pd.DataFrame(per_day).to_csv(out_dir / "backtest_overnight_prior.csv",
                                      index=False)
        print(f"\nSaved per-day breadth -> {out_dir / 'backtest_overnight_prior.csv'}")


if __name__ == "__main__":
    main()
