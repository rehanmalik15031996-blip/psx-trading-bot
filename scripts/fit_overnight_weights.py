"""Find the right overnight-signal weights from data.

Regress PSX median overnight gap (across the 15-stock universe) on the
overnight signals (S&P 500 ret_1d, VIX level, Nikkei ret_1d, Hang Seng
ret_1d, EEM ret_1d, DXY ret_1d) using only TRAIN data, then evaluate on
TEST data with:
  1. ridge regression (linear),
  2. "best single signal" baseline (which signal alone correlates most),
  3. the zero baseline.

Output:
  - correlation matrix of each signal with next-day median gap
  - fitted ridge weights
  - out-of-sample (hold-out) R^2 and direction hit rate
  - ASCII report

This answers: "is there a data-supported overnight prior at all, or should
we just use zero?"
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

UNIVERSE = ["HUBC", "PABC", "MLCF", "OGDC", "FABL", "PPL",
            "POL", "FCCL", "APL", "EPCL", "KOHC", "SEARL",
            "MCB", "MEBL", "PSO"]
CACHE = ROOT / "data" / "macro" / "overnight_global.parquet"
# Train/test: use all of Nov 2025 - Feb 2026 as TRAIN, Mar-Apr 2026 as TEST
TRAIN_START = pd.Timestamp("2024-06-01")    # broader train history
TRAIN_END = pd.Timestamp("2026-02-28")
TEST_START = pd.Timestamp("2026-03-01")
TEST_END = pd.Timestamp("2026-04-22")

SIGNAL_COLS = ["sp500_ret_1d", "vix_level_dev",
               "nikkei_ret_1d", "hangseng_ret_1d",
               "eem_ret_1d", "dxy_ret_1d"]


def load_universe_gaps() -> pd.DataFrame:
    """One row per PSX trading day: median gap % across the universe."""
    all_frames = []
    for sym in UNIVERSE:
        df = pd.read_parquet(ROOT / "data" / "ohlcv" / f"{sym}.parquet")
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["prev_close"] = df["close"].shift(1)
        df["gap_pct"] = (df["open"] / df["prev_close"] - 1) * 100
        all_frames.append(df[["date", "gap_pct"]].assign(symbol=sym))
    long = pd.concat(all_frames, ignore_index=True)
    agg = (long.groupby("date")["gap_pct"]
                 .agg(["median", "mean", "std", "count"]))
    agg = agg.rename(columns={"median": "psx_median_gap",
                               "mean": "psx_mean_gap",
                               "std": "psx_std_gap",
                               "count": "n_stocks"}).reset_index()
    return agg


def load_overnight_features() -> pd.DataFrame:
    """Assemble features indexed by PSX trading date."""
    if not CACHE.exists():
        raise FileNotFoundError(CACHE)
    ov = pd.read_parquet(CACHE)
    ov["date"] = pd.to_datetime(ov["date"]).dt.normalize()
    # Use the row as-of PSX trading day D-1 calendar day (overnight signals
    # available before PSX opens day D). We shift to align:
    #   For PSX day D, the signal is the latest overnight row with date <= D-1.
    # Easier: ov is keyed by its own calendar date; merge_asof on PSX dates.
    ov = ov.sort_values("date").reset_index(drop=True)
    # VIX level deviation from its 60-day rolling median (captures regime)
    ov["vix_level_dev"] = (ov["vix_close"] -
                            ov["vix_close"].rolling(60, min_periods=10).median())
    keep = ["date", "sp500_ret_1d", "vix_close", "vix_level_dev",
            "nikkei_ret_1d", "hangseng_ret_1d", "eem_ret_1d", "dxy_ret_1d"]
    return ov[keep].copy()


def build_panel() -> pd.DataFrame:
    psx = load_universe_gaps()
    ov = load_overnight_features()
    # For each PSX trading day D, find the latest overnight row with date < D
    psx["date"] = pd.to_datetime(psx["date"]).dt.normalize().astype("datetime64[ns]")
    ov["date"] = pd.to_datetime(ov["date"]).dt.normalize().astype("datetime64[ns]")
    psx = psx.sort_values("date").reset_index(drop=True)
    ov = ov.sort_values("date").reset_index(drop=True)
    merged = pd.merge_asof(psx, ov, left_on="date", right_on="date",
                            direction="backward", allow_exact_matches=False)
    # Convert returns to % units (already in frac in parquet)
    for c in ["sp500_ret_1d", "nikkei_ret_1d", "hangseng_ret_1d",
              "eem_ret_1d", "dxy_ret_1d"]:
        if c in merged:
            merged[c] = merged[c] * 100
    return merged


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float = 1.0) -> np.ndarray:
    X = np.hstack([np.ones((X.shape[0], 1)), X])
    I = np.eye(X.shape[1]); I[0, 0] = 0  # do not regularise the intercept
    return np.linalg.solve(X.T @ X + lam * I, X.T @ y)


def ridge_predict(X: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.hstack([np.ones((X.shape[0], 1)), X]) @ b


def direction_hit_rate(y_true: np.ndarray, y_pred: np.ndarray,
                        threshold: float = 0.4) -> float:
    p = np.where(y_pred > threshold, 1, np.where(y_pred < -threshold, -1, 0))
    a = np.where(y_true > threshold, 1, np.where(y_true < -threshold, -1, 0))
    return float((p == a).mean())


def main():
    panel = build_panel().dropna(subset=SIGNAL_COLS + ["psx_median_gap"])
    print(f"Panel rows: {len(panel)}  "
          f"({panel['date'].min().date()} .. {panel['date'].max().date()})")

    # Correlations (full sample for exploration)
    corr = panel[SIGNAL_COLS + ["psx_median_gap"]].corr()["psx_median_gap"]
    print("\nCORRELATION WITH PSX MEDIAN OVERNIGHT GAP (full sample):")
    for s, v in corr.drop("psx_median_gap").items():
        print(f"  {s:<22s} {v:+.3f}")

    train = panel[(panel["date"] >= TRAIN_START) & (panel["date"] <= TRAIN_END)]
    test = panel[(panel["date"] >= TEST_START) & (panel["date"] <= TEST_END)]
    print(f"\nTrain: {len(train)} rows  Test: {len(test)} rows")

    y_train = train["psx_median_gap"].values
    y_test = test["psx_median_gap"].values
    X_train = train[SIGNAL_COLS].values
    X_test = test[SIGNAL_COLS].values

    # Ridge fit
    for lam in [0.5, 2.0, 10.0, 50.0]:
        b = ridge_fit(X_train, y_train, lam)
        yhat_tr = ridge_predict(X_train, b)
        yhat_te = ridge_predict(X_test, b)
        mae_tr = np.mean(np.abs(yhat_tr - y_train))
        mae_te = np.mean(np.abs(yhat_te - y_test))
        r2_tr = 1 - np.var(y_train - yhat_tr) / np.var(y_train)
        r2_te = 1 - np.var(y_test - yhat_te) / np.var(y_test)
        hit_te = direction_hit_rate(y_test, yhat_te)
        print(f"\nRidge lam={lam}  train MAE={mae_tr:.3f}  "
              f"test MAE={mae_te:.3f}  train R2={r2_tr:.3f}  "
              f"test R2={r2_te:.3f}  test 3-class hit={hit_te*100:.1f}%")
        print(f"  intercept: {b[0]:+.4f}")
        for name, w in zip(SIGNAL_COLS, b[1:]):
            print(f"  {name:<22s} {w:+.4f}")

    # Baselines
    print("\n--- BASELINES ON TEST ---")
    zero_mae = np.mean(np.abs(y_test))
    zero_hit = direction_hit_rate(y_test, np.zeros_like(y_test))
    print(f"Zero prior   MAE={zero_mae:.3f}  3-class hit={zero_hit*100:.1f}%")
    # Mean of training set
    mean_pred = np.full_like(y_test, y_train.mean(), dtype=float)
    mean_mae = np.mean(np.abs(mean_pred - y_test))
    mean_hit = direction_hit_rate(y_test, mean_pred)
    print(f"Mean prior   MAE={mean_mae:.3f}  3-class hit={mean_hit*100:.1f}%  "
          f"(y_bar_train={y_train.mean():+.3f}%)")

    # Best single-signal linear fit (univariate)
    print("\n--- UNIVARIATE LINEAR FIT (single signal + intercept) ---")
    for col in SIGNAL_COLS:
        xi = train[col].values.reshape(-1, 1)
        bi = ridge_fit(xi, y_train, 0.5)
        yhat = ridge_predict(test[col].values.reshape(-1, 1), bi)
        mae = np.mean(np.abs(yhat - y_test))
        hit = direction_hit_rate(y_test, yhat)
        print(f"  {col:<22s} -> test MAE={mae:.3f}  3-class hit={hit*100:.1f}%  "
              f"slope={bi[1]:+.4f}")

    # Save fitted weights for the ridge lam=2 model
    b = ridge_fit(X_train, y_train, 2.0)
    weights = {"intercept": float(b[0]),
               **{c: float(w) for c, w in zip(SIGNAL_COLS, b[1:])},
               "train_window": f"{TRAIN_START.date()} .. {TRAIN_END.date()}",
               "test_window":  f"{TEST_START.date()} .. {TEST_END.date()}",
               "n_train": len(train), "n_test": len(test),
               "fitted_lambda": 2.0}
    import json
    out = ROOT / "reports" / "overnight_weights_fitted.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(weights, indent=2), encoding="utf-8")
    print(f"\nSaved fitted weights -> {out}")


if __name__ == "__main__":
    main()
