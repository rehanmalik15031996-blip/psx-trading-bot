"""Phase 2: Cross-sectional LightGBM ranker (optional).

Goal: enhance Phase 1's rule by re-ranking the volatility-filtered candidates.
Phase 1 picks top-N purely by 150d momentum. Phase 2 tries to improve the
risk-adjusted return by re-ranking those candidates with a cross-sectional
regressor.

Why cross-sectional and not per-stock
-------------------------------------
- Stacked data: 15 stocks × ~1200 trading days = ~18,000 rows, far larger than
  per-stock (~1,200 rows). That's enough data for a real ML signal without
  overfitting.
- Target is **demeaned** within each day: `fwd_20d_ret - cross_section_mean`.
  The model learns WHICH stock will outperform, not WHETHER the market rallies.
  This is exactly what a re-ranker needs.
- Uses a compact robust feature set (~15 features) — no kitchen-sink.
- Purged walk-forward with a 20-day embargo between train and test to respect
  the fwd_20d horizon (no look-ahead from overlapping targets).

Deployment gate
---------------
Do NOT deploy unless `scripts/validate_ranker.py` shows the ranker beats
Phase 1 by ≥2% CAGR with similar or lower max drawdown in a full out-of-sample
walk-forward. Otherwise, discard. `ranker_enabled=False` is the safe default.

Public API:
  - build_ranker_dataset(prices_wide, features_builder) -> (X, y, meta)
  - train_ranker(X, y) -> LightGBM Booster
  - predict_ranker(booster, X_row) -> score (higher = expected outperformance)
  - walk_forward_validate(...) -> pd.DataFrame of oos predictions + actuals
  - load_ranker() / save_ranker() -> pickle helpers
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)
RANKER_PATH = MODELS_DIR / "ranker_v2.pkl"


# --------------------------------------------------------------------------
# Feature specification (compact, robust, cross-section-friendly)
# --------------------------------------------------------------------------
RANKER_FEATURES: list[str] = [
    # Momentum at multiple horizons (cross-stock comparable after the rank)
    "mom_20d", "mom_60d", "mom_120d", "mom_250d",
    # Acceleration (short vs long momentum)
    "mom_20d_minus_120d",
    # Volatility regime
    "rvol_20d", "rvol_60d", "rvol_ratio",
    # Mean-reversion signals (very short-term)
    "ret_5d", "dist_from_20d_hi",
    # Cross-sectional rank features (normalize across stocks per day)
    "mom_120d_xrank", "rvol_20d_xrank", "ret_5d_xrank",
    # Market context (universe momentum)
    "universe_mom_120d",
]

RANKER_FEATURES_OPTIONAL: list[str] = ["vol_ratio_20"]


@dataclass
class RankerConfig:
    target_horizon: int = 20                 # 20-day forward return
    embargo_days: int = 20                   # must equal target_horizon
    n_splits: int = 5                        # walk-forward folds
    min_train_days: int = 500                # ~2 years minimum history
    # LightGBM hyperparameters (intentionally conservative)
    lgbm_params: dict = field(default_factory=lambda: {
        "objective": "regression",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l2": 1.0,
        "verbose": -1,
    })
    num_boost_round: int = 400
    early_stopping_rounds: int = 50


# --------------------------------------------------------------------------
# Dataset builder
# --------------------------------------------------------------------------
def _per_symbol_features(prices_wide: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Compute per-symbol features as a {symbol: DataFrame} dict, indexed by date."""
    log_ret = np.log(prices_wide).diff()

    out: dict[str, pd.DataFrame] = {}
    for sym in prices_wide.columns:
        c = prices_wide[sym]
        lr = log_ret[sym]

        df = pd.DataFrame(index=prices_wide.index)
        df["mom_20d"]  = lr.rolling(20).sum()
        df["mom_60d"]  = lr.rolling(60).sum()
        df["mom_120d"] = lr.rolling(120).sum()
        df["mom_250d"] = lr.rolling(250).sum()
        df["mom_20d_minus_120d"] = df["mom_20d"] - df["mom_120d"]

        df["rvol_20d"] = lr.rolling(20).std() * np.sqrt(252)
        df["rvol_60d"] = lr.rolling(60).std() * np.sqrt(252)
        df["rvol_ratio"] = df["rvol_20d"] / df["rvol_60d"]

        df["ret_5d"] = c.pct_change(5)
        hi20 = c.rolling(20).max()
        df["dist_from_20d_hi"] = (c - hi20) / hi20

        out[sym] = df
    return out


def build_ranker_dataset(
    prices_wide: pd.DataFrame,
    cfg: Optional[RankerConfig] = None,
    volumes_wide: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Build the stacked (date, symbol) regression dataset.

    Returns:
      X: feature matrix (rows = date×symbol, cols = RANKER_FEATURES)
      y: target (fwd_20d_ret minus cross-section mean that day)
      meta: DataFrame with date and symbol columns aligned to X rows
    """
    cfg = cfg or RankerConfig()
    per_sym = _per_symbol_features(prices_wide)

    universe_mom_120d = np.log(prices_wide).diff().rolling(120).sum().mean(axis=1)

    frames = []
    for sym, feat in per_sym.items():
        f = feat.copy()
        f["symbol"] = sym
        f["date"] = f.index
        f["universe_mom_120d"] = universe_mom_120d.values
        if volumes_wide is not None and sym in volumes_wide.columns:
            v = volumes_wide[sym].astype(float).replace(0, np.nan)
            f["vol_ratio_20"] = v / v.rolling(20).mean()
        fwd_ret = prices_wide[sym].pct_change(cfg.target_horizon).shift(-cfg.target_horizon)
        f["y_raw"] = fwd_ret.values
        frames.append(f)

    big = pd.concat(frames, ignore_index=True)

    for col in ("mom_120d", "rvol_20d", "ret_5d"):
        big[f"{col}_xrank"] = big.groupby("date")[col].rank(pct=True)

    big["y_mean_xsec"] = big.groupby("date")["y_raw"].transform("mean")
    big["y"] = big["y_raw"] - big["y_mean_xsec"]

    big = big.replace([np.inf, -np.inf], np.nan)
    feat_cols = list(RANKER_FEATURES)
    if volumes_wide is not None and "vol_ratio_20" in big.columns:
        feat_cols = feat_cols + RANKER_FEATURES_OPTIONAL
    big = big.dropna(subset=feat_cols + ["y"])

    big = big.sort_values(["date", "symbol"]).reset_index(drop=True)
    meta = big[["date", "symbol"]].copy()
    X = big[feat_cols].copy()
    y = big["y"].copy()
    return X, y, meta


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def train_ranker(
    X: pd.DataFrame,
    y: pd.Series,
    meta: pd.DataFrame,
    cfg: Optional[RankerConfig] = None,
    valid_fraction: float = 0.15,
):
    """Train a single LightGBM regressor with a time-based validation tail."""
    import lightgbm as lgb

    cfg = cfg or RankerConfig()
    order = meta["date"].argsort(kind="stable").values
    X_s = X.iloc[order].reset_index(drop=True)
    y_s = y.iloc[order].reset_index(drop=True)

    n = len(X_s)
    split = int(n * (1 - valid_fraction))
    X_train, y_train = X_s.iloc[:split], y_s.iloc[:split]
    X_valid, y_valid = X_s.iloc[split:], y_s.iloc[split:]

    dtrain = lgb.Dataset(X_train, label=y_train)
    dvalid = lgb.Dataset(X_valid, label=y_valid, reference=dtrain)

    booster = lgb.train(
        cfg.lgbm_params,
        dtrain,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(cfg.early_stopping_rounds, verbose=False),
                   lgb.log_evaluation(period=0)],
    )
    return booster


def predict_ranker(booster, X: pd.DataFrame) -> np.ndarray:
    return booster.predict(X, num_iteration=booster.best_iteration)


# --------------------------------------------------------------------------
# Purged walk-forward validation
# --------------------------------------------------------------------------
def walk_forward_validate(
    X: pd.DataFrame,
    y: pd.Series,
    meta: pd.DataFrame,
    cfg: Optional[RankerConfig] = None,
) -> pd.DataFrame:
    """Produce out-of-sample predictions via purged walk-forward CV.

    Each fold: train on all rows whose `date` < fold_start - embargo_days,
    predict rows in the fold window. This prevents the overlap between the
    20-day forward target and the next training window.
    """
    import lightgbm as lgb

    cfg = cfg or RankerConfig()
    dates = pd.to_datetime(meta["date"]).values
    uniq = np.sort(np.unique(dates))
    if len(uniq) < cfg.min_train_days + cfg.n_splits * 50:
        raise ValueError(
            f"Not enough data for walk-forward: {len(uniq)} unique dates")

    cutoffs = np.linspace(cfg.min_train_days, len(uniq), cfg.n_splits + 1, dtype=int)

    rows = []
    for i in range(cfg.n_splits):
        fold_start = uniq[cutoffs[i]]
        fold_end = uniq[min(cutoffs[i + 1] - 1, len(uniq) - 1)]
        embargo_cutoff = fold_start - np.timedelta64(cfg.embargo_days, "D")

        train_mask = dates < embargo_cutoff
        test_mask = (dates >= fold_start) & (dates <= fold_end)
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            continue

        X_tr, y_tr = X[train_mask], y[train_mask]
        X_te, y_te = X[test_mask], y[test_mask]

        dtrain = lgb.Dataset(X_tr, label=y_tr)
        booster = lgb.train(
            cfg.lgbm_params,
            dtrain,
            num_boost_round=cfg.num_boost_round,
            callbacks=[lgb.log_evaluation(period=0)],
        )
        preds = booster.predict(X_te)

        sub = meta.loc[test_mask, ["date", "symbol"]].copy()
        sub["y_true"] = y_te.values
        sub["y_pred"] = preds
        sub["fold"] = i
        rows.append(sub)

    if not rows:
        return pd.DataFrame(columns=["date", "symbol", "y_true", "y_pred", "fold"])
    return pd.concat(rows, ignore_index=True)


# --------------------------------------------------------------------------
# Re-ranking helper used at inference time by the strategy
# --------------------------------------------------------------------------
def rerank_with_ranker(
    candidates: pd.Series,
    booster,
    prices_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    top_n: int,
) -> list[str]:
    """Given Phase-1 volatility-filtered candidates (sorted by mom), re-rank
    them with the ML regressor and return the top-N.

    candidates: Series of (symbol -> momentum score) from strategy.apply_vol_filter
    """
    if booster is None or candidates.empty:
        return candidates.head(top_n).index.tolist()

    X_all, _y, meta = build_ranker_dataset(
        prices_wide[candidates.index],
        RankerConfig(),
    )
    if X_all.empty:
        return candidates.head(top_n).index.tolist()

    as_of_ts = pd.Timestamp(as_of)
    meta_dt = pd.to_datetime(meta["date"])
    mask = meta_dt == as_of_ts
    if not mask.any():
        valid = meta_dt[meta_dt <= as_of_ts]
        if valid.empty:
            return candidates.head(top_n).index.tolist()
        as_of_ts = valid.max()
        mask = meta_dt == as_of_ts

    X_today = X_all[mask]
    syms_today = meta.loc[mask, "symbol"].values
    preds = predict_ranker(booster, X_today)

    ranked = pd.Series(preds, index=syms_today).sort_values(ascending=False)
    return ranked.head(top_n).index.tolist()


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def save_ranker(booster, path: Path = RANKER_PATH) -> None:
    with open(path, "wb") as f:
        pickle.dump(booster, f)


def load_ranker(path: Path = RANKER_PATH):
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Smoke test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    from brain.strategy import build_prices_wide
    from config.universe import symbols as universe_symbols

    wide = build_prices_wide(universe_symbols())
    print(f"Price frame: {wide.shape}")
    X, y, meta = build_ranker_dataset(wide)
    print(f"Dataset: X={X.shape}, y={y.shape}, date range "
          f"{meta['date'].min().date()} → {meta['date'].max().date()}")
    print(f"Target mean/std: {y.mean():+.4f} / {y.std():.4f}")

    booster = train_ranker(X, y, meta)
    save_ranker(booster)
    print(f"Saved ranker to {RANKER_PATH}")

    import lightgbm as lgb  # noqa
    imp = pd.Series(
        booster.feature_importance(importance_type="gain"),
        index=X.columns,
    ).sort_values(ascending=False)
    print("\nTop features (gain):")
    for name, val in imp.head(10).items():
        print(f"  {name:30s} {val:>12.1f}")
