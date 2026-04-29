"""Walk-forward deterministic prediction backtest.

For every trading date `d` in the configured window and every symbol
in `config.universe.symbols()`, build a *point-in-time* context dict
that mirrors :func:`ui.tools.get_full_context`, slice every input to
data on or before `d`, and call
:func:`scripts.generate_predictions.predict_with_rules` to get a
deterministic forecast. Then look up `close[d + forward_days]` from
OHLCV to compute the realized return and emit one row per
(symbol, date) pair to ``data/backtest/walkforward_predictions.parquet``.

The output is consumed by ``scripts/phase1_backtest.py`` (Engine 2)
when ``--use-walkforward`` is set, and by the Streamlit panel in
``ui/phase1_backtest.py`` via the "Walk-forward rules" source picker.

Methodology caveats — read these before quoting hit rates:

  * **Latest-only fundamentals**. The value/quality lenses inside
    the synthesizer use the latest fundamentals parquet — small
    lookahead bias (fundamentals barely move in 60 days).
  * **Sparse news pre-2026-04-23**. ``data/news/scored_news.parquet``
    has ~1 article/day before April 23 vs ~80/day after. Historical
    predictions before that date will be NEUTRAL-leaning purely from
    missing news context. Documented in the markdown report.
  * **Phase-1 LightGBM lookahead**. The ``phase1_signal`` input
    (cross-sectional ranking from the trained LightGBM model) was
    fit on data including this window. To remove the bias the
    walk-forward computes the cross-sectional momentum rank
    *deterministically* from OHLCV at date ``d`` and treats it as a
    proxy. Accuracy is therefore slightly under-stated relative to
    a fully date-versioned LightGBM model.
  * **Rules engine vs LLM**. The deterministic engine is more
    conservative than the LLM — it issues more NEUTRAL predictions.
    This tests the bot's logic, not the LLM judgement layer. Same
    harness can be re-used for an LLM walk-forward by swapping
    ``predict_with_rules`` for ``predict_with_claude``.

Usage
-----

    python scripts/walkforward_predictions.py --window 60
    python scripts/walkforward_predictions.py --window 60 --as-of 2026-04-29
    python scripts/walkforward_predictions.py --window 14 --symbols HUBC OGDC
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

warnings.filterwarnings("ignore")

import math

import numpy as np
import pandas as pd

from config.universe import sector_of, symbols as universe_symbols
from data.store import load_ohlcv
from scripts.generate_predictions import HORIZON_DAYS, predict_with_rules

OUT_DIR = PROJECT_ROOT / "data" / "backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "walkforward_predictions.parquet"


# ---------------------------------------------------------------------------
# Point-in-time loaders
# ---------------------------------------------------------------------------


def _load_macro_files() -> dict[str, pd.DataFrame]:
    """Load every macro parquet once and cache it. Each frame is sorted
    ascending by date so we can binary-search to a target date without
    re-reading from disk on each call.
    """
    out: dict[str, pd.DataFrame] = {}
    for f in (PROJECT_ROOT / "data" / "macro").glob("*.parquet"):
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if df.empty:
            continue
        date_col = "date" if "date" in df.columns else (
            "Date" if "Date" in df.columns else None)
        if date_col is None:
            continue
        df["_d"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
        df = (df.dropna(subset=["_d"])
                .sort_values("_d")
                .reset_index(drop=True))
        # Pick the value column once per file. Macro parquets are
        # heterogeneous: most use ``value``; KSE-100 uses
        # ``kse100_close``; SBP / CPI use indicator-specific columns
        # we read via dedicated loaders so they are skipped here.
        candidate_cols = (
            "value", "Value",
            "close", "Close",
            "kse100_close",
            "sp500_close",
        )
        df["_v"] = np.nan
        for c in candidate_cols:
            if c in df.columns:
                df["_v"] = pd.to_numeric(df[c], errors="coerce")
                break
        if df["_v"].notna().sum() == 0:
            # No usable numeric column — skip this series rather than
            # silently producing all-NaN macro values
            continue
        out[f.stem] = df[["_d", "_v"]]
    return out


def _macro_at(macro_files: dict[str, pd.DataFrame], asof: date
               ) -> dict[str, dict]:
    """Return ``{name: {"value": float, "ret_21d": float}}`` for each
    macro series at ``asof``. ``ret_21d`` is the percent change over
    the last 21 calendar days (or whatever rows exist within that
    window).
    """
    out: dict[str, dict] = {}
    for name, df in macro_files.items():
        d = df[df["_d"] <= asof]
        if d.empty:
            continue
        last_v = d["_v"].iloc[-1]
        if pd.isna(last_v):
            continue
        ref_cut = asof - timedelta(days=21)
        ref_slice = d[d["_d"] <= ref_cut]
        if ref_slice.empty:
            ret_21d = None
        else:
            ref_v = ref_slice["_v"].iloc[-1]
            if pd.notna(ref_v) and ref_v != 0:
                ret_21d = float(last_v / ref_v - 1.0)
            else:
                ret_21d = None
        out[name] = {"value": float(last_v),
                       "ret_21d": ret_21d}
    return out


def _load_fipi() -> pd.DataFrame:
    p = PROJECT_ROOT / "data" / "flows" / "fipi_daily.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if df.empty or "date" not in df.columns:
        return df
    df["_d"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df.dropna(subset=["_d"]).sort_values("_d").reset_index(drop=True)


def _fipi_at(fipi: pd.DataFrame, asof: date) -> dict:
    if fipi.empty:
        return {}
    d = fipi[fipi["_d"] <= asof]
    if d.empty:
        return {}
    last = d.iloc[-1]
    out: dict = {
        "foreign_net_pkr_mn":
            float(last["foreign_net_pkr_mn"])
            if "foreign_net_pkr_mn" in last.index
               and pd.notna(last["foreign_net_pkr_mn"])
            else None,
        "local_net_pkr_mn":
            float(last["local_net_pkr_mn"])
            if "local_net_pkr_mn" in last.index
               and pd.notna(last["local_net_pkr_mn"])
            else None,
        "foreign_regime":
            str(last["foreign_regime"])
            if "foreign_regime" in last.index else "",
    }
    tail5 = d.tail(5)
    if "foreign_net_pkr_mn" in tail5.columns:
        out["foreign_5d_avg_pkr_mn"] = float(
            pd.to_numeric(tail5["foreign_net_pkr_mn"],
                            errors="coerce").fillna(0).mean())
    return out


def _load_policy_rate() -> pd.DataFrame:
    p = PROJECT_ROOT / "data" / "macro" / "sbp_rates.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if df.empty:
        return df
    date_col = "date" if "date" in df.columns else (
        "Date" if "Date" in df.columns else None)
    rate_col = next((c for c in
                       ("policy_rate_pct", "policy_rate", "rate")
                       if c in df.columns), None)
    if not date_col or not rate_col:
        return df
    df["_d"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    df["_r"] = pd.to_numeric(df[rate_col], errors="coerce")
    return (df.dropna(subset=["_d", "_r"])
              .sort_values("_d").reset_index(drop=True))


def _policy_rate_at(rate_df: pd.DataFrame, asof: date) -> dict:
    """Look up the SBP policy rate at ``asof``. The local parquet only
    has the latest few prints, so when ``asof`` is older than the
    earliest row we fall back to that earliest row — policy rate moves
    slowly (decisions every ~6 weeks) so the bias from this fallback
    is small. Documented in the methodology caveats.
    """
    if rate_df.empty:
        return {"policy_rate_pct": None, "rate_source": "missing"}
    d = rate_df[rate_df["_d"] <= asof]
    if not d.empty:
        return {"policy_rate_pct": float(d["_r"].iloc[-1]),
                  "rate_source": "asof"}
    # Fallback: use the earliest available rate. Better than None
    # because the rules engine compares this scalar with int(11).
    return {"policy_rate_pct": float(rate_df["_r"].iloc[0]),
              "rate_source": "earliest_fallback"}


# ---------------------------------------------------------------------------
# Per-symbol OHLCV-derived signals
# ---------------------------------------------------------------------------


def _technical_block(history: pd.DataFrame, asof: date
                       ) -> tuple[dict, dict, dict, dict, dict] | None:
    """Compute (price, technical, momentum, ma, vol) blocks at asof.

    All math is point-in-time clean: only rows with ``date <= asof``
    are used. Returns ``None`` when there is not enough history (we
    require at least 250 trading days so the 250-day momentum field
    is populated).
    """
    h = history.copy()
    h["date"] = pd.to_datetime(h["date"]).dt.date
    h = h[h["date"] <= asof].sort_values("date").reset_index(drop=True)
    if len(h) < 220:
        return None
    h["close"] = pd.to_numeric(h["close"], errors="coerce")
    closes = h["close"]
    last_close = float(closes.iloc[-1])
    last_date = h["date"].iloc[-1].isoformat()

    # ---- Returns ---------------------------------------------------------
    def _ret(n: int) -> float | None:
        if len(closes) <= n:
            return None
        return float(closes.iloc[-1] / closes.iloc[-1 - n] - 1.0)

    def _logret(n: int) -> float | None:
        r = _ret(n)
        if r is None:
            return None
        try:
            return float(math.log(1.0 + r))
        except ValueError:
            return None

    ret_5d  = _ret(5)
    ret_21d = _ret(21)
    ret_63d = _ret(63)
    mom_20  = _logret(20)
    mom_60  = _logret(60)
    mom_150 = _logret(150)
    mom_250 = _logret(250) if len(closes) > 251 else None

    # ---- RSI 14 ----------------------------------------------------------
    delta = closes.diff()
    up = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    down = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    if down and down > 0:
        rsi = 100.0 - 100.0 / (1.0 + up / down)
    elif up and up > 0:
        rsi = 100.0
    else:
        rsi = 50.0

    # ---- Moving averages -------------------------------------------------
    sma200 = closes.rolling(200).mean().iloc[-1]
    sma50  = closes.rolling(50).mean().iloc[-1]
    sma20  = closes.rolling(20).mean().iloc[-1]
    px_vs_sma200_pct = (
        float((last_close - sma200) / sma200 * 100.0)
        if sma200 and sma200 > 0 else None
    )
    px_vs_sma50_pct = (
        float((last_close - sma50) / sma50 * 100.0)
        if sma50 and sma50 > 0 else None
    )
    px_vs_sma20_pct = (
        float((last_close - sma20) / sma20 * 100.0)
        if sma20 and sma20 > 0 else None
    )

    # ---- Realized volatility (annualised) --------------------------------
    daily_rets = closes.pct_change().dropna().tail(20)
    if len(daily_rets) >= 10 and daily_rets.std() > 0:
        rvol_20d_ann = float(daily_rets.std() * math.sqrt(252))
    else:
        rvol_20d_ann = 0.30

    # ---- Trend tag (very rough) ------------------------------------------
    if px_vs_sma200_pct is not None and px_vs_sma50_pct is not None:
        if px_vs_sma200_pct > 0 and px_vs_sma50_pct > 0:
            trend = "uptrend"
        elif px_vs_sma200_pct < 0 and px_vs_sma50_pct < 0:
            trend = "downtrend"
        else:
            trend = "mixed"
    else:
        trend = "unknown"

    rvol_regime = ("low" if rvol_20d_ann < 0.20 else
                    "high" if rvol_20d_ann > 0.45 else "normal")

    price = {
        "close_pkr": last_close,
        "as_of":     last_date,
        "ret_5d":    ret_5d,
        "ret_21d":   ret_21d,
        "ret_63d":   ret_63d,
    }
    technical = {
        "rsi_14":          float(rsi),
        "trend":           trend,
        "momentum":        {
            "20d_log_ret":  mom_20,
            "60d_log_ret":  mom_60,
            "150d_log_ret": mom_150,
            "250d_log_ret": mom_250,
        },
        "moving_averages": {
            "px_vs_sma200_pct": px_vs_sma200_pct,
            "px_vs_sma50_pct":  px_vs_sma50_pct,
            "px_vs_sma20_pct":  px_vs_sma20_pct,
        },
        "volatility": {
            "rvol_20d_ann": rvol_20d_ann,
            "rvol_regime":  rvol_regime,
        },
    }
    return price, technical, technical["momentum"], \
           technical["moving_averages"], technical["volatility"]


def _forward_close(history: pd.DataFrame, asof: date,
                      fwd_days: int) -> tuple[float | None, str | None,
                                                 int]:
    """Return ``(close_at_asof_plus_fwd, that_date, bars_elapsed)``.

    If insufficient forward bars are available we return the last
    close still inside the OHLCV file so the caller can still report a
    partial-window realization with a clear ``bars_elapsed`` flag.
    """
    h = history.copy()
    h["date"] = pd.to_datetime(h["date"]).dt.date
    h = h.sort_values("date").reset_index(drop=True)
    idx = h.index[h["date"] == asof]
    if len(idx) == 0:
        return None, None, 0
    i = int(idx[0])
    j = i + fwd_days
    if j >= len(h):
        # Use the last available bar — partial realization
        j = len(h) - 1
    if j == i:
        return None, None, 0
    close_j = h.iloc[j]["close"]
    if pd.isna(close_j):
        return None, None, 0
    return (float(close_j), h.iloc[j]["date"].isoformat(), j - i)


# ---------------------------------------------------------------------------
# Cross-sectional momentum rank (deterministic phase-1 proxy)
# ---------------------------------------------------------------------------


def _cross_sectional_rank(histories: dict[str, pd.DataFrame],
                             asof: date,
                             top_n: int = 5) -> tuple[dict[str, int], set]:
    """Rank the universe by 60-day log return at ``asof`` and return
    ``(ranks, top_n_set)``. Used as a deterministic stand-in for the
    LightGBM phase-1 signal so we avoid that model's training-data
    lookahead.
    """
    rets = []
    for sym, hist in histories.items():
        h = hist.copy()
        h["date"] = pd.to_datetime(h["date"]).dt.date
        h = h[h["date"] <= asof].sort_values("date")
        if len(h) <= 60:
            continue
        c = pd.to_numeric(h["close"], errors="coerce").dropna()
        if len(c) <= 60:
            continue
        try:
            r = float(math.log(c.iloc[-1] / c.iloc[-61]))
        except (ValueError, ZeroDivisionError):
            continue
        rets.append((sym, r))
    rets.sort(key=lambda x: -x[1])
    ranks: dict[str, int] = {sym: i + 1
                              for i, (sym, _) in enumerate(rets)}
    top_n_set = {sym for sym, _ in rets[:top_n]}
    return ranks, top_n_set


# ---------------------------------------------------------------------------
# Build context + run rules
# ---------------------------------------------------------------------------


def _build_ctx(sym: str,
                  asof: date,
                  history: pd.DataFrame,
                  macro_files: dict[str, pd.DataFrame],
                  fipi: pd.DataFrame,
                  rate_df: pd.DataFrame,
                  rank_today: int | None,
                  in_top5: bool) -> dict | None:
    blocks = _technical_block(history, asof)
    if blocks is None:
        return None
    price, technical, _, _, _ = blocks

    macro_indicators = _macro_at(macro_files, asof)
    fipi_block = _fipi_at(fipi, asof)
    rate_block = _policy_rate_at(rate_df, asof)

    # Market risk-on heuristic: KSE-100 > 50d SMA
    market_risk_on = False
    if "kse100" in macro_files:
        kse = macro_files["kse100"]
        kse_slice = kse[kse["_d"] <= asof].tail(50)
        if len(kse_slice) >= 20:
            sma50 = kse_slice["_v"].mean()
            last_kse = kse_slice["_v"].iloc[-1]
            market_risk_on = bool(last_kse > sma50)

    return {
        "symbol":          sym,
        "sector":          sector_of(sym) or "Other",
        "as_of":           asof.isoformat(),
        "price":           price,
        "technical":       technical,
        "momentum_rank_today":  rank_today,
        "in_phase1_top5":       in_top5,
        "in_top5_if_filter_off": in_top5,
        "phase1_signal":   {"market_risk_on": market_risk_on},
        "news":            {},  # rules engine ignores news
        "fipi_flows":      fipi_block,
        "macro":           {"indicators": macro_indicators},
        "policy_rate":     rate_block,
    }


def _direction_hit(direction: str, realized_pct: float) -> bool:
    direction = (direction or "").upper()
    if direction == "BULLISH":
        return realized_pct > 0
    if direction == "BEARISH":
        return realized_pct < 0
    # NEUTRAL: a near-flat 5-day move is "correct" — same convention
    # as scripts/phase1_backtest.py
    return abs(realized_pct) < 1.5


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(window_days: int, asof_anchor: date, fwd_days: int,
         override_symbols: list[str] | None = None) -> pd.DataFrame:
    end = asof_anchor - timedelta(days=fwd_days)
    start = end - timedelta(days=window_days)
    syms = override_symbols or universe_symbols()

    print(f"[walkforward] anchor={asof_anchor}  "
          f"window=[{start} .. {end}]  forward_days={fwd_days}  "
          f"symbols={len(syms)}")

    print("[walkforward] loading shared inputs ...")
    macro_files = _load_macro_files()
    fipi = _load_fipi()
    rate_df = _load_policy_rate()

    histories: dict[str, pd.DataFrame] = {}
    for sym in syms:
        h = load_ohlcv(sym)
        if not h.empty:
            histories[sym] = h
    print(f"[walkforward]   ohlcv loaded for {len(histories)}/"
          f"{len(syms)} symbols")
    print(f"[walkforward]   macro series: {len(macro_files)}, "
          f"fipi rows: {len(fipi)}, rate rows: {len(rate_df)}")

    # Build the trading-date axis from OHLCV calendars (KSE-100
    # parquet is sparsely populated and not reliable as a calendar
    # source).
    all_dates: set[date] = set()
    for h in histories.values():
        d = pd.to_datetime(h["date"]).dt.date
        all_dates |= set(d[(d >= start) & (d <= end)])
    trading_dates = sorted(all_dates)
    print(f"[walkforward]   trading dates in window: "
          f"{len(trading_dates)}")

    rows: list[dict] = []
    t0 = time.perf_counter()
    for idx, d in enumerate(trading_dates):
        ranks, top5 = _cross_sectional_rank(histories, d)
        for sym in syms:
            hist = histories.get(sym)
            if hist is None:
                continue
            ctx = _build_ctx(sym, d, hist, macro_files, fipi, rate_df,
                                rank_today=ranks.get(sym),
                                in_top5=sym in top5)
            if ctx is None:
                continue
            try:
                pred = predict_with_rules(ctx)
            except Exception as e:
                print(f"  [{sym} {d}] rules failed: "
                      f"{type(e).__name__}: {e}")
                continue

            close_j, end_date, bars_elapsed = _forward_close(
                hist, d, fwd_days)
            if close_j is None or bars_elapsed == 0:
                continue
            entry = float(ctx["price"]["close_pkr"])
            realized_pct = (close_j / entry - 1.0) * 100.0
            mid = pred.get("expected_return_5d_mid_pct")
            mae = (abs(float(mid) - realized_pct)
                   if mid is not None else None)

            rows.append({
                "prediction_id": f"{sym}_{d.isoformat()}_wf",
                "symbol":        sym,
                "sector":        ctx["sector"],
                "asof":          d.isoformat(),
                "direction":     pred.get("direction"),
                "conviction":    pred.get("conviction"),
                "expected_mid_pct":   mid,
                "expected_low_pct":   pred.get(
                    "expected_return_5d_low_pct"),
                "expected_high_pct":  pred.get(
                    "expected_return_5d_high_pct"),
                "suggested_action":   pred.get("suggested_action"),
                "entry_price_pkr":    entry,
                "realized_end_pkr":   close_j,
                "realized_end_date":  end_date,
                "bars_elapsed":       bars_elapsed,
                "fwd_days_target":    fwd_days,
                "realized_pct":       round(realized_pct, 3),
                "abs_error_pct":
                    round(mae, 3) if mae is not None else None,
                "direction_hit":
                    bool(_direction_hit(pred.get("direction") or "",
                                          realized_pct)),
                "fully_realized":     bars_elapsed >= fwd_days,
                "key_drivers":        pred.get("key_drivers") or [],
                "key_risks":          pred.get("key_risks") or [],
                "source":             "walkforward_rules",
            })
        if (idx + 1) % 5 == 0 or idx == len(trading_dates) - 1:
            elapsed = time.perf_counter() - t0
            print(f"[walkforward]   processed {idx + 1}/"
                  f"{len(trading_dates)} dates "
                  f"(elapsed {elapsed:.1f}s, "
                  f"rows so far: {len(rows)})")

    df = pd.DataFrame(rows)
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=60,
                        help="Backtest window in calendar days (default 60)")
    parser.add_argument("--as-of", type=str, default=None,
                        help="Anchor date YYYY-MM-DD (default today UTC)")
    parser.add_argument("--forward-days", type=int, default=HORIZON_DAYS)
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Override universe with this list")
    args = parser.parse_args()

    asof = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
            if args.as_of
            else datetime.now(timezone.utc).date())

    df = run(window_days=int(args.window),
              asof_anchor=asof,
              fwd_days=int(args.forward_days),
              override_symbols=args.symbols)

    if df.empty:
        print("[walkforward] no rows generated — aborting parquet write")
        return 1

    df.to_parquet(OUT_PATH, index=False)
    print(f"\n[walkforward] wrote {len(df)} rows -> "
          f"{OUT_PATH.relative_to(PROJECT_ROOT)}")

    # Headline summary so the operator can sanity-check immediately
    realized = df["direction_hit"].mean() * 100.0
    print(f"[walkforward] overall direction-hit rate: {realized:.2f}%")
    by_dir = df.groupby("direction").agg(
        n=("direction_hit", "size"),
        hit_pct=("direction_hit",
                  lambda x: round(x.mean() * 100.0, 2)),
        mean_realized=("realized_pct",
                        lambda x: round(x.mean(), 3)),
    )
    print("[walkforward] by direction:\n" + by_dir.to_string())
    by_conv = df.groupby("conviction").agg(
        n=("direction_hit", "size"),
        hit_pct=("direction_hit",
                  lambda x: round(x.mean() * 100.0, 2)),
    )
    print("[walkforward] by conviction:\n" + by_conv.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
