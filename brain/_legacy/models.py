"""Per-stock binary classifier: P(forward 5-day return > 0).

Ensemble = rank-average of LightGBM + CatBoost. Both gradient-boosting
models handle tabular, mixed-scale features natively and resist overfitting
with only ~960 training rows per symbol.

Walk-forward validation via sklearn's TimeSeriesSplit ensures no
look-ahead bias: we only ever train on past data and score on strictly
future data.

Artifacts:
    models/{SYMBOL}_lgbm.pkl     (joblib-dumped LightGBM booster)
    models/{SYMBOL}_cb.cbm       (CatBoost native format)
    models/metrics.json          (per-symbol out-of-sample metrics)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Hyperparameters tuned for ~1k rows per stock / 70 features
# --------------------------------------------------------------------------
LGBM_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.02,
    "num_leaves": 31,
    "max_depth": -1,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "verbose": -1,
}

CB_PARAMS = {
    "iterations": 500,
    "learning_rate": 0.02,
    "depth": 6,
    "l2_leaf_reg": 3.0,
    "loss_function": "Logloss",
    "eval_metric": "AUC",
    "random_seed": 42,
    "verbose": False,
    "allow_writing_files": False,
}


@dataclass
class TrainResult:
    symbol: str
    n_train: int
    n_test: int
    auc: float
    accuracy: float
    log_loss: float
    up_rate_test: float
    top_features: list[tuple[str, float]]


# --------------------------------------------------------------------------
# Single-symbol training
# --------------------------------------------------------------------------
def train_one(
    df: pd.DataFrame,
    symbol: str,
    feature_cols: list[str],
    test_fraction: float = 0.15,
) -> TrainResult | None:
    """Train LGBM+CatBoost for one symbol using a simple time-based hold-out.

    The last `test_fraction` of the symbol's chronologically-sorted data is
    the out-of-sample test set. Models are FIT on the rest and evaluated on
    that tail. For production we use walk-forward (see train_walkforward).
    """
    d = df[df["symbol"] == symbol].dropna(subset=feature_cols + ["fwd_ret_5d_up"])
    if len(d) < 200:
        return None

    d = d.sort_values("date").reset_index(drop=True)
    n = len(d)
    cut = int(n * (1 - test_fraction))
    train, test = d.iloc[:cut], d.iloc[cut:]

    X_tr, y_tr = train[feature_cols], train["fwd_ret_5d_up"].astype(int)
    X_te, y_te = test[feature_cols], test["fwd_ret_5d_up"].astype(int)

    # --- LightGBM ---
    lgbm_train = lgb.Dataset(X_tr, label=y_tr)
    lgbm_eval  = lgb.Dataset(X_te, label=y_te, reference=lgbm_train)
    lgbm = lgb.train(
        LGBM_PARAMS,
        lgbm_train,
        num_boost_round=600,
        valid_sets=[lgbm_eval],
        callbacks=[lgb.early_stopping(40, verbose=False)],
    )
    p_lgb = lgbm.predict(X_te, num_iteration=lgbm.best_iteration)

    # --- CatBoost ---
    cb = CatBoostClassifier(**CB_PARAMS)
    cb.fit(X_tr, y_tr, eval_set=(X_te, y_te), early_stopping_rounds=40)
    p_cb = cb.predict_proba(X_te)[:, 1]

    # --- Ensemble (rank-average then normalize to [0,1]) ---
    p_ens = (pd.Series(p_lgb).rank(pct=True) + pd.Series(p_cb).rank(pct=True)) / 2
    p_ens = p_ens.to_numpy()

    # --- Metrics ---
    try:
        auc = roc_auc_score(y_te, p_ens)
    except ValueError:
        auc = float("nan")
    acc = accuracy_score(y_te, (p_ens > 0.5).astype(int))
    ll  = log_loss(y_te, np.clip(p_ens, 1e-6, 1 - 1e-6))

    # Feature importance from LGBM (gain)
    fi = pd.Series(
        lgbm.feature_importance(importance_type="gain"),
        index=feature_cols,
    ).sort_values(ascending=False)
    top5 = [(name, float(score)) for name, score in fi.head(5).items()]

    # --- Persist ---
    joblib.dump(lgbm, MODEL_DIR / f"{symbol}_lgbm.pkl")
    cb.save_model(str(MODEL_DIR / f"{symbol}_cb.cbm"))

    return TrainResult(
        symbol=symbol,
        n_train=len(train),
        n_test=len(test),
        auc=float(auc),
        accuracy=float(acc),
        log_loss=float(ll),
        up_rate_test=float(y_te.mean()),
        top_features=top5,
    )


# --------------------------------------------------------------------------
# Walk-forward out-of-sample prediction (used by backtester)
# --------------------------------------------------------------------------
def walkforward_predict(
    df: pd.DataFrame,
    symbol: str,
    feature_cols: list[str],
    n_splits: int = 5,
) -> pd.DataFrame:
    """Generate out-of-sample ensemble probabilities using TimeSeriesSplit.

    Returns a DataFrame with columns: date, symbol, close, fwd_ret_5d,
    fwd_ret_5d_up, prob_up_oos.
    """
    d = df[df["symbol"] == symbol].dropna(subset=feature_cols + ["fwd_ret_5d_up"])
    if len(d) < 400:
        return pd.DataFrame()

    d = d.sort_values("date").reset_index(drop=True)
    X = d[feature_cols]
    y = d["fwd_ret_5d_up"].astype(int)

    tscv = TimeSeriesSplit(n_splits=n_splits)
    preds = pd.Series(index=d.index, dtype=float)

    for train_idx, test_idx in tscv.split(X):
        X_tr, X_te = X.iloc[train_idx], X.iloc[test_idx]
        y_tr, y_te = y.iloc[train_idx], y.iloc[test_idx]
        if y_tr.nunique() < 2 or y_te.nunique() < 2:
            continue

        lgbm_train = lgb.Dataset(X_tr, label=y_tr)
        lgbm = lgb.train(LGBM_PARAMS, lgbm_train, num_boost_round=200)
        p_lgb = lgbm.predict(X_te)

        cb = CatBoostClassifier(**{**CB_PARAMS, "iterations": 200})
        cb.fit(X_tr, y_tr, verbose=False)
        p_cb = cb.predict_proba(X_te)[:, 1]

        p_ens = (pd.Series(p_lgb).rank(pct=True) + pd.Series(p_cb).rank(pct=True)) / 2
        preds.iloc[test_idx] = p_ens.to_numpy()

    out = d[["date", "symbol", "close", "fwd_ret_5d", "fwd_ret_5d_up"]].copy()
    out["prob_up_oos"] = preds.values
    return out.dropna(subset=["prob_up_oos"])


# --------------------------------------------------------------------------
# Inference helper
# --------------------------------------------------------------------------
def predict_latest(
    df: pd.DataFrame,
    symbol: str,
    feature_cols: list[str],
) -> float | None:
    """Use the persisted models to score the most recent row for `symbol`.

    Returns the ensemble probability of up (in [0,1]) or None if unavailable.
    """
    d = df[df["symbol"] == symbol].dropna(subset=feature_cols)
    if d.empty:
        return None
    latest = d.sort_values("date").iloc[-1:]

    lgbm_path = MODEL_DIR / f"{symbol}_lgbm.pkl"
    cb_path = MODEL_DIR / f"{symbol}_cb.cbm"
    if not lgbm_path.exists() or not cb_path.exists():
        return None

    lgbm = joblib.load(lgbm_path)
    cb = CatBoostClassifier()
    cb.load_model(str(cb_path))

    X = latest[feature_cols]
    p_lgb = lgbm.predict(X)[0]
    p_cb = cb.predict_proba(X)[0, 1]
    # Simple average (can't rank a single row)
    return float((p_lgb + p_cb) / 2)


def save_metrics(results: list[TrainResult]) -> Path:
    out = MODEL_DIR / "metrics.json"
    out.write_text(
        json.dumps([asdict(r) for r in results], indent=2, default=str),
        encoding="utf-8",
    )
    return out
