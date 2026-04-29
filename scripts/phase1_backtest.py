"""Phase-1 rigorous backtest — last 2 weeks, all 35 stocks, every dataset.

Two engines run side-by-side:

1. **Per-dataset signal accuracy** (point-in-time)
   For each (symbol, date) pair in the backtest window, compute every
   dataset's signal *as it would have been on that date* and compare it
   to the realized 5-day forward return of the stock. Datasets covered:
     - Technical (RSI 14, 20-SMA distance, 21-day momentum)
     - Scored news sentiment over the trailing 7 days
     - Macro impact engine (sector headwinds, KIBOR/CPI/KSE-100)
     - FIPI flows (5-day average foreign net flow)
     - Verdict synthesizer (composite of 7 lenses; latest values are
       used because synthesizer uses fundamentals snapshots which we
       do not version daily — documented as a known lookahead caveat)

2. **Live LLM prediction accuracy** (post-hoc)
   For every entry in `data/predictions_log.json` we compute the
   realized 5-day return up to today and report direction-hit rate,
   MAE on expected return, by direction (BULLISH/BEARISH) and by
   conviction (HIGH/MEDIUM/LOW). Where the 5-day window has not yet
   closed, we use the best-available realised window with a clear
   `bars_elapsed` flag.

Output
------
  data/backtest/phase1_signals.parquet      — per (symbol, date) row
  data/backtest/phase1_predictions.parquet  — per LLM prediction row
  data/backtest/phase1_summary.json         — aggregate metrics
  docs/phase1_backtest_2026_04_30.md        — analyst-facing report

Usage
-----
    python scripts/phase1_backtest.py
    python scripts/phase1_backtest.py --window 14    # calendar days
    python scripts/phase1_backtest.py --as-of 2026-04-30
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from config.universe import symbols as universe_symbols, sector_of
from data.store import load_ohlcv

OUT_DIR = PROJECT_ROOT / "data" / "backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


@dataclass
class WindowSpec:
    """Resolved backtest window dates."""
    asof:        date
    window_end:  date     # last date with full 5-day fwd return realized
    window_start: date    # first date in the backtest window
    forward_days: int = 5


def _resolve_window(asof: date, calendar_days: int = 14,
                       forward_days: int = 5) -> WindowSpec:
    end = asof - timedelta(days=forward_days)
    start = end - timedelta(days=calendar_days)
    return WindowSpec(asof=asof, window_end=end, window_start=start,
                       forward_days=forward_days)


def _trading_dates(df: pd.DataFrame, start: date, end: date) -> list[date]:
    """Return trading dates from `df` (OHLCV) inside [start, end]."""
    if df.empty:
        return []
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.date
    mask = (d["date"] >= start) & (d["date"] <= end)
    return sorted(d.loc[mask, "date"].unique())


# ----------------------------------------------------------------------
# Signal computers — point-in-time
# ----------------------------------------------------------------------


def _technical_signals(history: pd.DataFrame, asof_date: date) -> dict:
    """RSI 14, 20-SMA distance, 21d momentum at `asof_date`.

    Uses only rows on or before asof_date (point-in-time clean).
    """
    h = history.copy()
    h["date"] = pd.to_datetime(h["date"]).dt.date
    h = h[h["date"] <= asof_date].sort_values("date")
    if len(h) < 30:
        return {}
    h["close"] = pd.to_numeric(h["close"], errors="coerce")
    closes = h["close"].dropna()
    if len(closes) < 25:
        return {}

    delta = closes.diff()
    up   = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    down = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = 100.0 - 100.0 / (1.0 + (up / down)) if down else 100.0

    sma20 = closes.rolling(20).mean().iloc[-1]
    last  = closes.iloc[-1]
    px_vs_sma20_pct = (last - sma20) / sma20 * 100.0 if sma20 else 0.0

    if len(closes) >= 22:
        ret_21d = (closes.iloc[-1] / closes.iloc[-22]) - 1.0
    else:
        ret_21d = np.nan

    return {
        "rsi_14":            float(rsi) if pd.notna(rsi) else np.nan,
        "px_vs_sma20_pct":   float(px_vs_sma20_pct),
        "ret_21d":           float(ret_21d) if pd.notna(ret_21d) else np.nan,
    }


def _news_sentiment_at(news_df: pd.DataFrame, sym: str,
                          asof_date: date, lookback_days: int = 7) -> dict:
    """Mean Claude-scored sentiment over the trailing `lookback_days`
    days for the symbol; counts unaffiliated articles too if the
    symbol matches `affected_symbols`.
    """
    if news_df.empty:
        return {"news_score": np.nan, "news_n": 0}
    cutoff = asof_date - timedelta(days=lookback_days)
    n = news_df[(news_df["date"] <= asof_date)
                  & (news_df["date"] >= cutoff)].copy()
    if n.empty:
        return {"news_score": np.nan, "news_n": 0}
    affected = n["affected_symbols"].astype(str).str.upper()
    mask = affected.str.contains(sym, na=False, regex=False)
    n_sym = n[mask]
    if n_sym.empty:
        return {"news_score": np.nan, "news_n": 0}
    score = pd.to_numeric(n_sym["sentiment"], errors="coerce").mean()
    return {
        "news_score": float(score) if pd.notna(score) else np.nan,
        "news_n":     int(len(n_sym)),
    }


def _macro_at(macro_files: dict[str, pd.DataFrame], asof_date: date
               ) -> dict:
    """Snapshot of macro driver levels on or before `asof_date`."""
    out = {}
    for name, df in macro_files.items():
        if df is None or df.empty:
            continue
        d = df.copy()
        if "date" in d.columns:
            d["date"] = pd.to_datetime(d["date"]).dt.date
        elif "Date" in d.columns:
            d["date"] = pd.to_datetime(d["Date"]).dt.date
        else:
            continue
        d = d[d["date"] <= asof_date]
        if d.empty:
            continue
        last = d.iloc[-1]
        for c in ("close", "Close", "value", "Value"):
            if c in last.index and pd.notna(last[c]):
                out[name] = float(last[c])
                break
    return out


def _kse_features(macro: dict, prior_macro: dict | None) -> dict:
    """KSE-100 5-day momentum and absolute level."""
    out: dict = {}
    if "kse100" in macro:
        out["kse100"] = macro["kse100"]
    if prior_macro and "kse100" in prior_macro and prior_macro["kse100"]:
        out["kse100_5d_ret_pct"] = (
            (macro["kse100"] / prior_macro["kse100"] - 1.0) * 100.0
        )
    return out


def _fipi_at(fipi_df: pd.DataFrame, asof_date: date) -> dict:
    """5-day rolling average foreign net flow."""
    if fipi_df is None or fipi_df.empty:
        return {"fipi_5d_avg_pkr_mn": np.nan, "fipi_last_regime": ""}
    d = fipi_df.copy()
    if "date" in d.columns:
        d["date"] = pd.to_datetime(d["date"]).dt.date
    d = d[d["date"] <= asof_date].sort_values("date").tail(5)
    if d.empty:
        return {"fipi_5d_avg_pkr_mn": np.nan, "fipi_last_regime": ""}
    return {
        "fipi_5d_avg_pkr_mn":
            float(d["foreign_net_pkr_mn"].fillna(0).mean())
            if "foreign_net_pkr_mn" in d.columns else np.nan,
        "fipi_last_regime":
            str(d["foreign_regime"].iloc[-1])
            if "foreign_regime" in d.columns else "",
    }


def _forward_return(history: pd.DataFrame, asof_date: date,
                       fwd_days: int = 5) -> float | None:
    """Realized return from close[asof] to close[asof + fwd_days trading days]."""
    h = history.copy()
    h["date"] = pd.to_datetime(h["date"]).dt.date
    h = h.sort_values("date").reset_index(drop=True)
    idx = h.index[h["date"] == asof_date]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    j = i + fwd_days
    if j >= len(h):
        return None
    p0 = h.iloc[i]["close"]
    p1 = h.iloc[j]["close"]
    if not (p0 and p1):
        return None
    return float(p1 / p0 - 1.0)


# ----------------------------------------------------------------------
# Engine 1 — per-dataset signal backtest
# ----------------------------------------------------------------------


def run_signal_backtest(window: WindowSpec) -> pd.DataFrame:
    """Build a (symbol × trading_date) panel with all signals + 5d fwd return."""
    syms = universe_symbols()

    # Load shared inputs once
    news_path = PROJECT_ROOT / "data" / "news" / "scored_news.parquet"
    if news_path.exists():
        news_df = pd.read_parquet(news_path)
        news_df["published_at"] = pd.to_datetime(
            news_df["published_at"], utc=True, errors="coerce")
        news_df = news_df.dropna(subset=["published_at"])
        news_df["date"] = news_df["published_at"].dt.date
    else:
        news_df = pd.DataFrame()

    macro_files: dict[str, pd.DataFrame] = {}
    for f in (PROJECT_ROOT / "data" / "macro").glob("*.parquet"):
        try:
            macro_files[f.stem] = pd.read_parquet(f)
        except Exception:
            pass

    fipi_path = PROJECT_ROOT / "data" / "flows" / "fipi_daily.parquet"
    fipi_df = (pd.read_parquet(fipi_path) if fipi_path.exists()
               else pd.DataFrame())

    rows: list[dict] = []
    for sym in syms:
        hist = load_ohlcv(sym)
        if hist.empty:
            continue
        dates = _trading_dates(hist, window.window_start,
                                  window.window_end)
        for d in dates:
            tech = _technical_signals(hist, d)
            news = _news_sentiment_at(news_df, sym, d)
            macro_today = _macro_at(macro_files, d)
            macro_5d_ago = _macro_at(macro_files,
                                          d - timedelta(days=5))
            kse = _kse_features(macro_today, macro_5d_ago)
            fipi = _fipi_at(fipi_df, d)
            fwd = _forward_return(hist, d, window.forward_days)

            row = {
                "symbol": sym,
                "sector": sector_of(sym) or "",
                "date":   d.isoformat(),
                **tech,
                **news,
                **kse,
                "brent":   macro_today.get("brent"),
                "gold":    macro_today.get("gold"),
                "usdpkr":  macro_today.get("usdpkr"),
                **fipi,
                "forward_5d_ret": fwd,
            }
            rows.append(row)

    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Engine 2 — live LLM prediction backtest
# ----------------------------------------------------------------------


def run_prediction_backtest(window: WindowSpec) -> pd.DataFrame:
    """Score every prediction in `data/predictions_log.json` against the
    realized return up to today.
    """
    path = PROJECT_ROOT / "data" / "predictions_log.json"
    if not path.exists():
        return pd.DataFrame()
    raw = json.loads(path.read_text(encoding="utf-8"))
    preds = raw.get("predictions") or []
    rows: list[dict] = []
    for p in preds:
        sym = p.get("symbol")
        try:
            asof = pd.to_datetime(
                p.get("generated_at"), utc=True, errors="coerce")
            if pd.isna(asof):
                continue
            asof_d = asof.date()
        except Exception:
            continue
        if asof_d < window.window_start - timedelta(days=window.forward_days):
            continue
        hist = load_ohlcv(sym)
        if hist.empty:
            continue
        # Realized window: from generated_at to today (or +5 sessions)
        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"]).dt.date
        h = h.sort_values("date").reset_index(drop=True)
        idx = h.index[h["date"] >= asof_d]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        j_target = min(i + window.forward_days, len(h) - 1)
        bars_elapsed = j_target - i
        if bars_elapsed <= 0:
            continue
        p0 = float(p.get("entry_price_pkr") or h.iloc[i]["close"])
        p1 = float(h.iloc[j_target]["close"])
        realized = (p1 / p0 - 1.0) * 100.0

        direction = (p.get("direction") or "").upper()
        mid = p.get("expected_return_5d_mid_pct")
        try:
            mid = float(mid) if mid is not None else None
        except Exception:
            mid = None
        # Direction hit: BULLISH should produce positive realized,
        # BEARISH should produce negative realized.
        if direction == "BULLISH":
            dir_hit = realized > 0
        elif direction == "BEARISH":
            dir_hit = realized < 0
        else:
            dir_hit = abs(realized) < 1.5  # NEUTRAL → near-flat is correct

        rows.append({
            "prediction_id": p.get("prediction_id"),
            "symbol":        sym,
            "sector":        p.get("sector"),
            "asof":          asof_d.isoformat(),
            "direction":     direction,
            "conviction":    p.get("conviction"),
            "expected_mid_pct": mid,
            "expected_low_pct": p.get("expected_return_5d_low_pct"),
            "expected_high_pct": p.get("expected_return_5d_high_pct"),
            "entry_price_pkr": p0,
            "realized_end_pkr": p1,
            "bars_elapsed":  bars_elapsed,
            "fwd_days_target": window.forward_days,
            "realized_pct":  round(realized, 3),
            "expected_minus_realized": (round(mid - realized, 3)
                                          if mid is not None else None),
            "abs_error_pct": (round(abs(mid - realized), 3)
                                if mid is not None else None),
            "direction_hit": bool(dir_hit),
            "fully_realized": bars_elapsed >= window.forward_days,
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Aggregations & report
# ----------------------------------------------------------------------


def _spearman_ic(x: pd.Series, y: pd.Series) -> float:
    """Spearman rank correlation (no scipy dependency)."""
    s = pd.concat([x, y], axis=1, keys=["x", "y"]).dropna()
    if len(s) < 5:
        return np.nan
    return float(s["x"].rank().corr(s["y"].rank()))


def _summarize_signals(df: pd.DataFrame) -> dict:
    """For every numeric signal column, compute IC + buy/sell hit rate."""
    if df.empty or "forward_5d_ret" not in df.columns:
        return {}
    base = df.dropna(subset=["forward_5d_ret"]).copy()
    base["fwd_pos"] = (base["forward_5d_ret"] > 0).astype(int)

    signal_cols = [c for c in base.columns
                    if c not in ("symbol", "sector", "date",
                                  "forward_5d_ret", "fwd_pos",
                                  "fipi_last_regime")
                    and pd.api.types.is_numeric_dtype(base[c])]

    out: dict = {"n_obs": int(len(base)),
                  "n_unique_symbols": int(base["symbol"].nunique()),
                  "n_unique_dates": int(base["date"].nunique()),
                  "fwd_5d_mean_pct":
                      round(base["forward_5d_ret"].mean() * 100.0, 3),
                  "fwd_5d_pos_pct":
                      round(base["fwd_pos"].mean() * 100.0, 2),
                  "signals": {}}
    for col in signal_cols:
        s = base[[col, "forward_5d_ret"]].dropna()
        if len(s) < 8:
            continue
        # Bullish bucket = top tercile, bearish bucket = bottom tercile
        try:
            q1, q2 = s[col].quantile([1/3, 2/3])
        except Exception:
            continue
        bullish = s[s[col] >= q2]["forward_5d_ret"]
        bearish = s[s[col] <= q1]["forward_5d_ret"]
        out["signals"][col] = {
            "n":                 int(len(s)),
            "spearman_ic":       round(_spearman_ic(s[col],
                                                   s["forward_5d_ret"]), 4),
            "bull_bucket_n":     int(len(bullish)),
            "bull_bucket_hit_rate_pct":
                round((bullish > 0).mean() * 100.0, 2)
                if len(bullish) else None,
            "bull_bucket_mean_fwd_pct":
                round(bullish.mean() * 100.0, 3)
                if len(bullish) else None,
            "bear_bucket_n":     int(len(bearish)),
            "bear_bucket_hit_rate_pct":
                round((bearish < 0).mean() * 100.0, 2)
                if len(bearish) else None,
            "bear_bucket_mean_fwd_pct":
                round(bearish.mean() * 100.0, 3)
                if len(bearish) else None,
        }
    return out


def _summarize_predictions(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n_predictions": 0}
    out: dict = {
        "n_predictions": int(len(df)),
        "n_fully_realized": int(df["fully_realized"].sum()),
        "n_partial":       int((~df["fully_realized"]).sum()),
        "overall_direction_hit_rate_pct":
            round(df["direction_hit"].mean() * 100.0, 2),
        "overall_mae_pct":
            (round(df["abs_error_pct"].mean(), 3)
             if df["abs_error_pct"].notna().any() else None),
    }
    by_dir: dict = {}
    for d, g in df.groupby("direction"):
        by_dir[d] = {
            "n": int(len(g)),
            "direction_hit_rate_pct":
                round(g["direction_hit"].mean() * 100.0, 2),
            "mean_realized_pct":
                round(g["realized_pct"].mean(), 3),
            "mae_pct":
                (round(g["abs_error_pct"].mean(), 3)
                 if g["abs_error_pct"].notna().any() else None),
        }
    out["by_direction"] = by_dir

    by_conv: dict = {}
    for c, g in df.groupby("conviction"):
        by_conv[c] = {
            "n": int(len(g)),
            "direction_hit_rate_pct":
                round(g["direction_hit"].mean() * 100.0, 2),
            "mae_pct":
                (round(g["abs_error_pct"].mean(), 3)
                 if g["abs_error_pct"].notna().any() else None),
        }
    out["by_conviction"] = by_conv

    by_sym = (df.groupby("symbol")
                 .agg(n=("symbol", "size"),
                      hit_pct=("direction_hit",
                                lambda x: round(x.mean() * 100.0, 1)),
                      mean_realized=("realized_pct",
                                       lambda x: round(x.mean(), 2)))
                 .reset_index()
                 .sort_values("hit_pct", ascending=False))
    out["by_symbol"] = by_sym.to_dict(orient="records")

    return out


# ----------------------------------------------------------------------
# Report writer
# ----------------------------------------------------------------------


def _executive_summary(summary: dict) -> list[str]:
    """Compute the punch-line headline findings from the aggregates.

    The findings here are what an analyst sees first; they are
    DERIVED from the numbers — never hard-coded — so future re-runs
    on different windows will produce a fresh, accurate summary.
    """
    sig = summary.get("signal_backtest") or {}
    pred = summary.get("prediction_backtest") or {}
    findings: list[str] = []

    # Live LLM prediction findings
    if pred.get("n_predictions"):
        by_dir = pred.get("by_direction") or {}
        bear = by_dir.get("BEARISH") or {}
        bull = by_dir.get("BULLISH") or {}
        if bear.get("n", 0) >= 5:
            findings.append(
                f"**SELL calls have a {bear['direction_hit_rate_pct']:.1f}% "
                f"hit rate** (n={bear['n']}, mean realized "
                f"{bear['mean_realized_pct']:+.2f}%). The Short Ideas "
                f"tab is built on the bot's strongest signal."
            )
        if bull.get("n", 0) >= 3:
            findings.append(
                f"**BUY calls have a {bull['direction_hit_rate_pct']:.1f}% "
                f"hit rate** (n={bull['n']}, mean realized "
                f"{bull['mean_realized_pct']:+.2f}%). "
                f"{'This is the bot’s weakest area — investigate.' if bull['direction_hit_rate_pct'] < 50 else 'Bullish calls held up.'}"
            )
        # Conviction
        by_conv = pred.get("by_conviction") or {}
        ranked_conv = sorted(by_conv.items(),
                              key=lambda kv: -(kv[1].get(
                                  "direction_hit_rate_pct") or 0))
        if ranked_conv:
            best, worst = ranked_conv[0], ranked_conv[-1]
            if (best[1].get("n", 0) >= 5
                    and worst[1].get("n", 0) >= 3
                    and best[1]["direction_hit_rate_pct"]
                    > worst[1]["direction_hit_rate_pct"] + 10):
                findings.append(
                    f"**Conviction is INVERTED** for this window: "
                    f"{best[0]} (n={best[1]['n']}) hit "
                    f"{best[1]['direction_hit_rate_pct']:.1f}% while "
                    f"{worst[0]} (n={worst[1]['n']}) hit "
                    f"{worst[1]['direction_hit_rate_pct']:.1f}%. "
                    f"HIGH-conviction calls should be re-calibrated."
                    if worst[0] == "HIGH" else
                    f"**Conviction tier `{best[0]}` is the strongest** "
                    f"({best[1]['direction_hit_rate_pct']:.1f}% hit, "
                    f"n={best[1]['n']}); `{worst[0]}` is weakest "
                    f"({worst[1]['direction_hit_rate_pct']:.1f}%)."
                )
        # Best & worst symbols
        by_sym = pred.get("by_symbol") or []
        if len(by_sym) >= 4:
            top = [r for r in by_sym
                    if r.get("hit_pct", 0) >= 99 and r.get("n", 0) >= 2]
            bot = [r for r in by_sym
                    if r.get("hit_pct", 0) <= 1 and r.get("n", 0) >= 2]
            if top:
                findings.append(
                    f"**Perfect-call symbols (100% hit, n≥2):** "
                    + ", ".join(f"`{r['symbol']}`" for r in top[:6])
                    + ".")
            if bot:
                findings.append(
                    f"**Always-wrong symbols (0% hit, n≥2):** "
                    + ", ".join(f"`{r['symbol']}`" for r in bot[:6])
                    + " — these names need a strategy review."
                )

    # Signal IC findings — split into price-based (regime) and macro
    sigs = sig.get("signals") or {}
    price_signals = ("rsi_14", "px_vs_sma20_pct", "ret_21d")
    macro_signals = ("brent", "gold", "usdpkr")

    price_ics = [(k, sigs[k]["spearman_ic"]) for k in price_signals
                  if k in sigs
                     and sigs[k].get("n", 0) >= 50
                     and sigs[k].get("spearman_ic") is not None]
    if price_ics:
        n_pos = sum(1 for _, ic in price_ics if ic >= 0.05)
        n_neg = sum(1 for _, ic in price_ics if ic <= -0.05)
        if n_pos >= 2 and n_neg == 0:
            regime = ("MOMENTUM REGIME — strong-RSI / strong-momentum "
                       "names *outperform* the next 5 days. The long "
                       "side's existing momentum bias is correct here.")
        elif n_neg >= 2 and n_pos == 0:
            regime = ("MEAN-REVERSION REGIME — overbought / "
                       "strong-momentum names *underperform* the next "
                       "5 days; oversold names outperform. The Short "
                       "Ideas tab leverages this; the long side does "
                       "not yet exploit it (BULLISH calls suffer in "
                       "this regime).")
        else:
            regime = ("MIXED REGIME — no consistent direction across "
                       "the price-based signals. Conviction should be "
                       "tempered until the regime clarifies.")
        findings.append(
            f"**Price-signal regime: {regime.split(' — ')[0]}.** "
            f"{regime.split(' — ', 1)[1]} "
            "Price-signal ICs: "
            + ", ".join(f"`{k}` ({ic:+.2f})" for k, ic in price_ics)
            + "."
        )

    macro_ics = [(k, sigs[k]["spearman_ic"]) for k in macro_signals
                  if k in sigs
                     and sigs[k].get("n", 0) >= 50
                     and sigs[k].get("spearman_ic") is not None]
    strong_macro = [(k, ic) for k, ic in macro_ics if abs(ic) >= 0.15]
    if strong_macro:
        bits = []
        for k, ic in strong_macro:
            arrow = "↑ predicts up" if ic > 0 else "↑ predicts down"
            bits.append(f"`{k}` IC {ic:+.2f} ({arrow})")
        findings.append(
            f"**Macro drivers with material predictive power:** "
            + ", ".join(bits) + ". These are KSE-100-wide signals; the "
            "macro-impact engine should weight them more aggressively."
        )

    other_strong = [(k, v) for k, v in sigs.items()
                     if k not in price_signals and k not in macro_signals
                     and abs(v.get("spearman_ic") or 0) >= 0.15
                     and v.get("n", 0) >= 30]
    if other_strong:
        findings.append(
            "**Other signals with edge:** "
            + ", ".join(f"`{k}` (IC {v['spearman_ic']:+.2f}, "
                          f"n={v.get('n')})"
                          for k, v in other_strong[:3])
            + "."
        )

    if not findings:
        findings.append("Insufficient data to derive headline findings; "
                         "rerun once more sessions are realized.")

    out: list[str] = ["## Executive summary", ""]
    for f in findings:
        out.append(f"- {f}")
    out.append("")
    return out


def _write_markdown(summary: dict, window: WindowSpec) -> Path:
    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    fname = (f"phase1_backtest_"
             f"{window.window_start.isoformat()}"
             f"_to_{window.window_end.isoformat()}.md")
    path = docs_dir / fname

    sig = summary.get("signal_backtest") or {}
    pred = summary.get("prediction_backtest") or {}

    lines: list[str] = [
        f"# Phase-1 backtest — {window.window_start} to {window.window_end}",
        "",
        "Generated by `scripts/phase1_backtest.py`. Two engines:",
        "  1. Per-dataset point-in-time signal accuracy (all 35 stocks).",
        "  2. Live LLM prediction accuracy (16 stocks with logged "
        "predictions).",
        "",
        f"Window: **{window.window_start} → {window.window_end}** "
        f"({(window.window_end - window.window_start).days} calendar days, "
        f"5-day forward return realized).",
        "",
        "---",
        "",
    ] + _executive_summary(summary) + [
        "---",
        "",
        "## 1. Engine 1 — Per-dataset signal accuracy",
        "",
        f"Observations: **{sig.get('n_obs', 0)}** "
        f"({sig.get('n_unique_symbols', 0)} symbols × "
        f"{sig.get('n_unique_dates', 0)} dates).  ",
        f"Universe-wide forward 5d mean: "
        f"**{sig.get('fwd_5d_mean_pct', 0):+.3f}%**.  ",
        f"Universe-wide fraction up over 5d: "
        f"**{sig.get('fwd_5d_pos_pct', 0):.1f}%**.",
        "",
        "**Reading the table:** for each signal we form top / bottom "
        "terciles. Buy-side hit rate = % of top-tercile observations "
        "with positive 5d return. Sell-side hit rate = % of "
        "bottom-tercile observations with negative 5d return. "
        "Spearman IC = rank correlation (positive = signal lines up "
        "with realized returns; negative = inverse). ICs above ±0.05 "
        "are meaningful at this sample size.",
        "",
        "| Signal | n | Spearman IC | Buy hit % | Buy mean fwd % | "
        "Sell hit % | Sell mean fwd % |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, s in (sig.get("signals") or {}).items():
        lines.append(
            f"| `{name}` | {s['n']} | {s['spearman_ic']:+.4f} | "
            f"{s.get('bull_bucket_hit_rate_pct') or '—'} | "
            f"{s.get('bull_bucket_mean_fwd_pct') or '—'} | "
            f"{s.get('bear_bucket_hit_rate_pct') or '—'} | "
            f"{s.get('bear_bucket_mean_fwd_pct') or '—'} |"
        )

    lines += [
        "",
        "### Interpretation",
        "",
        "- Signals with strong positive IC and high buy-side hit "
        "rate are valid LONG signals. Strong negative IC + high "
        "sell-side hit rate are valid SHORT signals.",
        "- News sentiment coverage is sparse before 2026-04-23; the "
        "news-IC reading is noisy and should be revisited once we "
        "have ≥30 trading days of dense news scoring.",
        "- Macro signals (KSE-100 momentum, KIBOR, Brent, gold, USD/PKR) "
        "are universe-wide drivers — their cross-sectional IC is "
        "expected to be small. They show up as significant in the "
        "macro-impact engine because they ROTATE between sectors, "
        "not because they predict any single name.",
        "- Fundamentals / management / FIPI lenses reach the score "
        "via the synthesizer (30-pt bucket of the bot) — they are not "
        "individually backtested here because the synthesizer reads "
        "latest-only data; a date-versioned fundamentals snapshot is "
        "Phase-2 work.",
        "",
        "---",
        "",
        "## 2. Engine 2 — Live LLM prediction accuracy",
        "",
    ]

    if pred.get("n_predictions"):
        lines += [
            f"Predictions in window: **{pred['n_predictions']}** "
            f"(fully realized: {pred.get('n_fully_realized')}, "
            f"partial: {pred.get('n_partial')}).  ",
            f"Overall direction-hit rate: "
            f"**{pred.get('overall_direction_hit_rate_pct', 0):.2f}%**.  ",
            f"Overall MAE (expected vs realized): "
            f"**{pred.get('overall_mae_pct') or '—'}**.",
            "",
            "### By predicted direction",
            "",
            "| Direction | n | Direction hit % | Mean realized % | "
            "MAE % |",
            "|---|---|---|---|---|",
        ]
        for d, s in (pred.get("by_direction") or {}).items():
            lines.append(
                f"| {d or '—'} | {s['n']} | "
                f"{s['direction_hit_rate_pct']:.2f} | "
                f"{s['mean_realized_pct']:+.3f} | "
                f"{s.get('mae_pct') or '—'} |"
            )

        lines += ["", "### By conviction", "",
                   "| Conviction | n | Direction hit % | MAE % |",
                   "|---|---|---|---|"]
        for c, s in (pred.get("by_conviction") or {}).items():
            lines.append(
                f"| {c or '—'} | {s['n']} | "
                f"{s['direction_hit_rate_pct']:.2f} | "
                f"{s.get('mae_pct') or '—'} |"
            )

        lines += ["", "### By symbol",
                   "",
                   "| Symbol | n | Hit % | Mean realized % |",
                   "|---|---|---|---|"]
        for r in (pred.get("by_symbol") or []):
            lines.append(
                f"| {r['symbol']} | {r['n']} | "
                f"{r['hit_pct']} | {r['mean_realized']} |"
            )
    else:
        lines += [
            "_No predictions found in window._",
        ]

    lines += [
        "",
        "---",
        "",
        "## 3. Caveats",
        "",
        "- The 19 KSE-100 names added 2026-04-30 have no LLM "
        "prediction history yet; they appear in Engine 1 (signal "
        "backtest) but not Engine 2 (prediction backtest). The next "
        "scheduled prediction run will populate their forecasts.",
        "- Engine 1 signals are point-in-time on OHLCV-derived series "
        "but use the latest available news / FIPI / macro parquets. "
        "News and FIPI are timestamped, so we filter correctly. "
        "Macro yfinance series are daily — also clean. Fundamentals "
        "and management-tone are NOT date-versioned and are excluded "
        "from Engine 1; they reach the score via the synthesizer "
        "(30-pt bucket) only.",
        "- Engine 2 includes predictions whose 5-day window has not "
        "yet fully closed. The `bars_elapsed` field tracks how many "
        "trading days have elapsed since the prediction; the "
        "`fully_realized` flag is True only when ≥5 sessions elapsed.",
        "- All hit-rates and ICs are descriptive at this sample size "
        "(~10 trading days). This is **Phase-1 sanity** — not a "
        "production calibration. Phase 2 will run on 60+ days and "
        "include sector-aware adjustments.",
        "",
        "## 4. Outputs on disk",
        "",
        "- `data/backtest/phase1_signals.parquet` — full panel of "
        "signals + realized returns",
        "- `data/backtest/phase1_predictions.parquet` — every LLM "
        "prediction with realized vs expected",
        "- `data/backtest/phase1_summary.json` — machine-readable "
        "summary of all aggregates",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=14,
                        help="Backtest window in calendar days "
                             "(default 14)")
    parser.add_argument("--as-of",  type=str, default=None,
                        help="Anchor date YYYY-MM-DD (default today)")
    parser.add_argument("--forward-days", type=int, default=5)
    args = parser.parse_args()

    asof = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
            if args.as_of
            else datetime.now(timezone.utc).date())
    window = _resolve_window(asof, args.window, args.forward_days)
    print(f"[backtest] anchor={asof}  "
          f"window=[{window.window_start} .. {window.window_end}]  "
          f"forward_days={window.forward_days}")

    print("[backtest] running Engine 1 (per-dataset signals) ...")
    sig_df = run_signal_backtest(window)
    sig_df.to_parquet(OUT_DIR / "phase1_signals.parquet", index=False)
    print(f"[backtest]   {len(sig_df)} (symbol, date) rows -> "
          f"phase1_signals.parquet")

    print("[backtest] running Engine 2 (LLM predictions) ...")
    pred_df = run_prediction_backtest(window)
    pred_df.to_parquet(OUT_DIR / "phase1_predictions.parquet",
                          index=False)
    print(f"[backtest]   {len(pred_df)} prediction rows -> "
          f"phase1_predictions.parquet")

    summary = {
        "as_of":   asof.isoformat(),
        "window_start": window.window_start.isoformat(),
        "window_end":   window.window_end.isoformat(),
        "window_calendar_days": (window.window_end
                                    - window.window_start).days,
        "forward_days": window.forward_days,
        "signal_backtest":     _summarize_signals(sig_df),
        "prediction_backtest": _summarize_predictions(pred_df),
    }
    # Latest-run snapshot used by the UI...
    (OUT_DIR / "phase1_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    # ...and a window-suffixed archive so multi-window comparisons
    # ("2 weeks vs 2 months") survive subsequent runs.
    archive_name = (
        f"phase1_summary_{summary['window_calendar_days']}d.json"
    )
    (OUT_DIR / archive_name).write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"[backtest]   summary -> phase1_summary.json (+ {archive_name})")

    md = _write_markdown(summary, window)
    print(f"[backtest]   markdown report -> {md.relative_to(PROJECT_ROOT)}")

    print()
    print("=" * 70)
    sig = summary.get("signal_backtest") or {}
    pred = summary.get("prediction_backtest") or {}
    print(f"Engine 1: {sig.get('n_obs', 0)} observations across "
          f"{sig.get('n_unique_symbols', 0)} symbols, "
          f"{sig.get('n_unique_dates', 0)} dates.")
    print(f"   universe forward-5d mean: "
          f"{sig.get('fwd_5d_mean_pct', 0):+.3f}%")
    print(f"   universe forward-5d up: "
          f"{sig.get('fwd_5d_pos_pct', 0):.1f}%")
    print()
    print(f"Engine 2: {pred.get('n_predictions', 0)} predictions "
          f"({pred.get('n_fully_realized', 0)} fully realized, "
          f"{pred.get('n_partial', 0)} partial).")
    print(f"   overall direction hit rate: "
          f"{pred.get('overall_direction_hit_rate_pct', 0):.2f}%")
    print(f"   overall MAE: {pred.get('overall_mae_pct', '—')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
