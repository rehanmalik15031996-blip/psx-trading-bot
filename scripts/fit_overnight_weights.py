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

# Baseline (pre-2026-05-14) signal set — frozen for OOS comparison.
BASELINE_SIGNAL_COLS = ["sp500_ret_1d", "vix_level_dev",
                        "nikkei_ret_1d", "hangseng_ret_1d",
                        "eem_ret_1d", "dxy_ret_1d"]

# Extended signal set — adds 3 regional EM tickers already pulled in the
# briefing but previously not part of the fitted gap prior.
#
# fm_etf_ret_1d (iShares MSCI Frontier 100 ETF) was originally planned to
# be a 4th addition but its parquet stream has not refreshed beyond
# 2025-01-08 (yfinance symbol issue), so we exclude it from the panel.
# Re-add once the data feed is fixed.
SIGNAL_COLS = BASELINE_SIGNAL_COLS + [
    "nifty_ret_1d", "kospi_ret_1d", "shanghai_ret_1d",
]


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
            "nikkei_ret_1d", "hangseng_ret_1d", "eem_ret_1d", "dxy_ret_1d",
            "nifty_ret_1d", "kospi_ret_1d", "shanghai_ret_1d"]
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
              "eem_ret_1d", "dxy_ret_1d",
              "nifty_ret_1d", "kospi_ret_1d", "shanghai_ret_1d"]:
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

    def _fit_and_score(cols: list[str], lam: float = 2.0) -> dict:
        Xtr = train[cols].values
        Xte = test[cols].values
        bb  = ridge_fit(Xtr, y_train, lam)
        yhat_te = ridge_predict(Xte, bb)
        return {
            "weights": {"intercept": float(bb[0]),
                          **{c: float(w) for c, w in zip(cols, bb[1:])}},
            "test_r2":  float(1 - np.var(y_test - yhat_te) / np.var(y_test)),
            "test_mae": float(np.mean(np.abs(yhat_te - y_test))),
            "test_direction_hit": direction_hit_rate(y_test, yhat_te),
            "signal_cols": list(cols),
            "fitted_lambda": lam,
        }

    print("\n--- BASELINE (6-signal) vs EXTENDED (10-signal) "
          "side-by-side ---")
    panel_b = build_panel().dropna(
        subset=BASELINE_SIGNAL_COLS + ["psx_median_gap"])
    train_b = panel_b[(panel_b["date"] >= TRAIN_START) &
                      (panel_b["date"] <= TRAIN_END)]
    test_b = panel_b[(panel_b["date"] >= TEST_START) &
                     (panel_b["date"] <= TEST_END)]
    y_train_b = train_b["psx_median_gap"].values
    y_test_b  = test_b["psx_median_gap"].values
    Xtr_b = train_b[BASELINE_SIGNAL_COLS].values
    Xte_b = test_b[BASELINE_SIGNAL_COLS].values
    bb_b  = ridge_fit(Xtr_b, y_train_b, 2.0)
    yhat_te_b = ridge_predict(Xte_b, bb_b)
    baseline = {
        "weights": {"intercept": float(bb_b[0]),
                      **{c: float(w) for c, w in zip(BASELINE_SIGNAL_COLS,
                                                     bb_b[1:])}},
        "test_r2":  float(1 - np.var(y_test_b - yhat_te_b) / np.var(y_test_b)),
        "test_mae": float(np.mean(np.abs(yhat_te_b - y_test_b))),
        "test_direction_hit": direction_hit_rate(y_test_b, yhat_te_b),
        "signal_cols": list(BASELINE_SIGNAL_COLS),
        "fitted_lambda": 2.0,
        "n_train": len(train_b), "n_test": len(test_b),
    }
    extended = _fit_and_score(SIGNAL_COLS, 2.0)
    extended["n_train"] = len(train); extended["n_test"] = len(test)
    print(f"  baseline (6): test R2={baseline['test_r2']:+.4f}  "
          f"MAE={baseline['test_mae']:.3f}  "
          f"hit={baseline['test_direction_hit']*100:.1f}%")
    print(f"  extended(10): test R2={extended['test_r2']:+.4f}  "
          f"MAE={extended['test_mae']:.3f}  "
          f"hit={extended['test_direction_hit']*100:.1f}%")
    print(f"  delta R2: {extended['test_r2'] - baseline['test_r2']:+.4f}   "
          f"delta MAE: {extended['test_mae'] - baseline['test_mae']:+.4f}   "
          f"delta hit: "
          f"{(extended['test_direction_hit'] - baseline['test_direction_hit'])*100:+.1f}pp")
    accept = (extended["test_r2"] - baseline["test_r2"] > 0.0
              and extended["test_direction_hit"]
                  >= baseline["test_direction_hit"] - 0.005)
    print(f"  -> recommendation: "
          f"{'ACCEPT extended weights' if accept else 'KEEP baseline (no improvement)'}")

    # Save the chosen ridge model (lam=2) and metadata.
    chosen = extended if accept else baseline
    out = ROOT / "reports" / "overnight_weights_fitted.json"
    out.parent.mkdir(exist_ok=True)
    import json
    out.write_text(json.dumps({
        **chosen["weights"],
        "train_window": f"{TRAIN_START.date()} .. {TRAIN_END.date()}",
        "test_window":  f"{TEST_START.date()} .. {TEST_END.date()}",
        "n_train": chosen["n_train"], "n_test": chosen["n_test"],
        "fitted_lambda": chosen["fitted_lambda"],
        "signal_cols": chosen["signal_cols"],
        "test_r2": chosen["test_r2"],
        "test_mae": chosen["test_mae"],
        "test_direction_hit": chosen["test_direction_hit"],
        "baseline_test_r2": baseline["test_r2"],
        "baseline_test_mae": baseline["test_mae"],
        "baseline_test_direction_hit": baseline["test_direction_hit"],
        "extended_accepted": accept,
    }, indent=2), encoding="utf-8")
    print(f"\nSaved fitted weights -> {out}  "
          f"(accepted={'extended' if accept else 'baseline'})")


if __name__ == "__main__":
    main()
