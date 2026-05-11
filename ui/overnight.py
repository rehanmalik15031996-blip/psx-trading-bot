"""Overnight global risk signals for PSX open-direction prediction.

PSX opens 09:32 PKT (~04:32 UTC). Between yesterday's PSX close (15:30 PKT =
10:30 UTC) and today's PSX open, roughly 18 hours pass in which the US
session closed and Asian markets opened. Those moves are the strongest
predictor of the PSX overnight gap — which is exactly what our walk-forward
kept missing.

This module reads data/macro/overnight_global.parquet (produced by
scripts/fetch_overnight_global.py) and produces:

- load_overnight(cutoff) -> dict with latest values <= cutoff and returns
- build_overnight_block(cutoff) -> str ready to paste into an LLM briefing
- gap_bias_from_overnight(d) -> dict giving a rules-based gap expectation
  (the LLM can reference this but also override it)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "macro" / "overnight_global.parquet"
FIPI_CACHE = ROOT / "data" / "flows" / "fipi_daily.parquet"


def load_latest_fipi(cutoff: "pd.Timestamp") -> dict | None:
    """Return the latest cached FIPI snapshot at or before `cutoff`.

    Returns None if the cache doesn't exist yet (we only have history
    from the first day cache_fipi_daily.py was run).
    """
    if not FIPI_CACHE.exists():
        return None
    df = pd.read_parquet(FIPI_CACHE)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    cutoff = pd.Timestamp(cutoff).normalize()
    df = df[df["date"] <= cutoff].sort_values("date")
    if df.empty:
        return None
    r = df.iloc[-1]
    return {
        "date": r["date"].date().isoformat(),
        "foreign_net_pkr_mn": float(r.get("foreign_net_pkr_mn") or 0),
        "local_net_pkr_mn": float(r.get("local_net_pkr_mn") or 0),
        "foreign_regime": r.get("foreign_regime"),
        "top_sector_name": r.get("top_sector_name"),
        "top_sector_net_usd_mn": (float(r["top_sector_net_usd_mn"])
                                   if pd.notna(r.get("top_sector_net_usd_mn"))
                                   else None),
        "days_stale": int((cutoff - r["date"]).days),
    }


def _vix_regime(v: float | None) -> str:
    if v is None:
        return "unknown"
    if v < 14:
        return "complacent"
    if v < 18:
        return "normal"
    if v < 22:
        return "elevated"
    if v < 28:
        return "stressed"
    return "panic"


def load_overnight(cutoff: pd.Timestamp) -> dict:
    """Return the latest overnight snapshot on or before `cutoff` (PSX date).

    For predicting PSX day D's open, we want:
      - US session closed on D-1 US calendar (i.e. date <= D-1 <= cutoff)
      - Asian markets: latest close <= cutoff (Asia trades same-day as PSX)
    """
    if not CACHE.exists():
        return {"error": f"overnight cache missing: {CACHE}"}
    df = pd.read_parquet(CACHE).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    cutoff = pd.Timestamp(cutoff).normalize()
    df = df[df["date"] <= cutoff]
    if df.empty:
        return {"error": f"no overnight data <= {cutoff.date()}"}

    # IMPORTANT: different tickers have different last-trading-day calendars
    # (FX trades 24/7, US/Asia/Europe close at different times). Picking the
    # absolute last row would mask all the indices that aren't open today.
    # For each label, take its latest non-null close <= cutoff.
    out: dict = {"as_of": df["date"].iloc[-1].date().isoformat()}
    # Core gap-prior labels + regional EM peers + FX/rates added 2026-05.
    # `fm_etf` was previously fetched but never surfaced — fixed now.
    labels = [
        "sp500", "vix", "nikkei", "hangseng", "ftse", "dxy", "eem", "fm_etf",
        "nifty", "kospi", "sti", "shanghai",
        "us10y", "usd_inr", "usd_cny", "eur_usd",
    ]
    for label in labels:
        cclose = f"{label}_close"
        c1 = f"{label}_ret_1d"
        c5 = f"{label}_ret_5d"
        if cclose not in df.columns:
            continue
        sub = df[df[cclose].notna()]
        if sub.empty:
            continue
        row = sub.iloc[-1]
        v = row.get(cclose)
        if v is None or pd.isna(v):
            continue
        out[label] = {
            "close": round(float(v), 2),
            "as_of": row["date"].date().isoformat(),
            "ret_1d_pct": (round(float(row[c1]) * 100, 2)
                           if c1 in row and pd.notna(row[c1]) else None),
            "ret_5d_pct": (round(float(row[c5]) * 100, 2)
                           if c5 in row and pd.notna(row[c5]) else None),
        }
    # Enrich VIX with regime label
    if "vix" in out:
        out["vix"]["regime"] = _vix_regime(out["vix"]["close"])
    return out


# ----------------------------------------------------------------------------
# Weights below were FITTED (ridge, lam=2.0) on the universe-median overnight
# gap using train=2024-06-01..2026-02-28 and tested on 2026-03-01..2026-04-22.
# Out-of-sample: 3-class direction hit rate 51.5% vs 27.3% zero baseline
# (see scripts/fit_overnight_weights.py and reports/overnight_weights_fitted.json).
# Key surprises from the fit:
#   - S&P 500 and EEM are the meaningful positive signals (+0.08, +0.04).
#   - Nikkei is slightly NEGATIVE (contrarian).
#   - Hang Seng and DXY have near-zero predictive power.
#   - Intercept is +0.34% — PSX has a persistent positive overnight drift.
# ----------------------------------------------------------------------------
FITTED_WEIGHTS = {
    "intercept":       0.3402,   # positive overnight drift
    "sp500_ret_1d":    0.0800,
    "vix_level_dev":  -0.0044,   # vs 60d rolling median
    "nikkei_ret_1d":  -0.0187,   # negative = mean-reversion relative to Tokyo
    "hangseng_ret_1d":-0.0074,   # ~0; kept for completeness
    "eem_ret_1d":      0.0389,
    "dxy_ret_1d":     -0.0128,
}


def gap_bias_from_overnight(d: dict) -> dict:
    """Data-fitted overnight gap prior for PSX.

    Returns a small-but-real expected-gap number (in %). The model R^2 is
    ~0.045 out-of-sample — meaningful but weak. Treat the output as a
    tilt, not a forecast.
    """
    if "error" in d:
        return {"bias": "UNKNOWN", "expected_gap_pct": 0.0,
                "reasoning": d["error"]}

    sp = (d.get("sp500") or {}).get("ret_1d_pct") or 0
    vix_level = (d.get("vix") or {}).get("close") or 18
    vix_regime = (d.get("vix") or {}).get("regime", "normal")
    nkk = (d.get("nikkei") or {}).get("ret_1d_pct") or 0
    hsi = (d.get("hangseng") or {}).get("ret_1d_pct") or 0
    eem = (d.get("eem") or {}).get("ret_1d_pct") or 0
    dxy = (d.get("dxy") or {}).get("ret_1d_pct") or 0

    # VIX level deviation uses a simple heuristic (we don't carry the 60d
    # rolling median here; use regime labels as a proxy)
    vix_dev_proxy = {"complacent": -4.0, "normal": 0.0, "elevated": +4.0,
                     "stressed": +8.0, "panic": +14.0}.get(vix_regime, 0.0)

    w = FITTED_WEIGHTS
    expected_gap = (
        w["intercept"]
        + w["sp500_ret_1d"]    * sp
        + w["vix_level_dev"]   * vix_dev_proxy
        + w["nikkei_ret_1d"]   * nkk
        + w["hangseng_ret_1d"] * hsi
        + w["eem_ret_1d"]      * eem
        + w["dxy_ret_1d"]      * dxy
    )

    # Narrower 3-class buckets: threshold ±0.25 reflects that the fitted
    # model is a gentle tilt, not a strong forecast
    if expected_gap > 0.55:      # strong (above intercept + ~0.2)
        bias = "GAP_UP"
    elif expected_gap < 0.10:    # below intercept => real bearish
        bias = "GAP_DOWN"
    else:
        bias = "FLAT"

    reasoning = (
        f"intercept={w['intercept']:+.2f}% + "
        f"S&P={sp:+.2f}%×{w['sp500_ret_1d']:+.3f} + "
        f"EEM={eem:+.2f}%×{w['eem_ret_1d']:+.3f} + "
        f"Nikkei={nkk:+.2f}%×{w['nikkei_ret_1d']:+.3f} + "
        f"HSI={hsi:+.2f}%×{w['hangseng_ret_1d']:+.3f} + "
        f"DXY={dxy:+.2f}%×{w['dxy_ret_1d']:+.3f} + "
        f"VIX_dev={vix_dev_proxy:+.1f}×{w['vix_level_dev']:+.3f} "
        f"(regime={vix_regime}, level {vix_level:.1f})"
    )
    return {
        "bias": bias,
        "expected_gap_pct": round(expected_gap, 2),
        "reasoning": reasoning,
    }


def build_overnight_block(cutoff: pd.Timestamp) -> str:
    """Formatted markdown-ish text block for inclusion in an LLM briefing."""
    d = load_overnight(cutoff)
    if "error" in d:
        return f"OVERNIGHT GLOBAL RISK\n  (unavailable: {d['error']})"
    bias = gap_bias_from_overnight(d)
    lines = [
        "OVERNIGHT GLOBAL RISK  (what happened since yesterday's PSX close)",
        f"  as_of_date (latest US/Asia session) = {d.get('as_of')}",
    ]
    for label, full in [("sp500", "S&P 500"), ("vix", "VIX"),
                         ("nikkei", "Nikkei 225"), ("hangseng", "Hang Seng"),
                         ("ftse", "FTSE 100"), ("dxy", "USD Index"),
                         ("eem", "EM ETF"), ("fm_etf", "Frontier ETF"),
                         ("nifty", "NIFTY 50"), ("kospi", "KOSPI"),
                         ("sti", "SG Straits"), ("shanghai", "Shanghai Comp"),
                         ("us10y", "US 10Y yield"), ("usd_inr", "USD/INR"),
                         ("usd_cny", "USD/CNY"), ("eur_usd", "EUR/USD")]:
        if label not in d:
            continue
        v = d[label]
        extra = f"  regime={v['regime']}" if label == "vix" else ""
        r1 = v.get("ret_1d_pct")
        r5 = v.get("ret_5d_pct")
        lines.append(
            f"  {full:<12s} close={v['close']:<10.2f} "
            f"1d={r1:+.2f}% " if r1 is not None else f"  {full:<12s} close={v['close']:<10.2f}"
        )
        # append 5d and extras on same logical row for compactness
        lines[-1] = lines[-1].rstrip() + (
            f"  5d={r5:+.2f}%" if r5 is not None else ""
        ) + extra
    lines.append("")
    lines.append(
        f"RULES-BASED GAP PRIOR: {bias['bias']}  "
        f"(expected PSX gap ~ {bias['expected_gap_pct']:+.2f}%)"
    )
    lines.append(f"  derivation: {bias['reasoning']}")
    lines.append(
        "  NOTE: this is the *prior* for the overnight gap direction. "
        "Override only if a stock-specific driver is strong."
    )

    # Scored news-sentiment macro tilt (last 24h)
    try:
        from ui.news_sentiment import macro_sentiment
        macro = macro_sentiment(hours=24.0)
        if macro["n"] > 0:
            arrow = ("BULLISH" if macro["score"] > 0.1
                      else "BEARISH" if macro["score"] < -0.1 else "NEUTRAL")
            lines += [
                "",
                f"MACRO NEWS SENTIMENT (24h, weighted):  "
                f"{macro['score']:+.2f} [{arrow}]  (n={macro['n']})",
            ]
            if macro.get("by_category"):
                cats = "  ".join(f"{k}={v:+.2f}"
                                   for k, v in macro["by_category"].items())
                lines.append(f"  by cat: {cats}")
    except Exception:
        pass

    # Recent FIPI flows (if the daily cache has any history)
    fipi = load_latest_fipi(cutoff)
    if fipi is not None:
        stale = fipi["days_stale"]
        freshness = "today" if stale == 0 else f"{stale}d stale"
        lines += [
            "",
            f"FIPI / LIPI FLOWS ({fipi['date']}, {freshness})",
            f"  Foreign net = {fipi['foreign_net_pkr_mn']:+.2f} mn PKR "
            f"({fipi['foreign_regime']})",
            f"  Local net   = {fipi['local_net_pkr_mn']:+.2f} mn PKR",
        ]
        if fipi["top_sector_name"]:
            lines.append(
                f"  Top sector flow: {fipi['top_sector_name']}  "
                f"{fipi['top_sector_net_usd_mn']:+.2f} mn USD"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    cutoff = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else pd.Timestamp.today()
    print(build_overnight_block(cutoff))
