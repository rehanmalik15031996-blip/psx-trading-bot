"""Quality + earnings-momentum scoring for the PSX universe.

Two complementary signals derived from the cached fundamentals:

* **Quality score (0-100)** — blends profitability, leverage, earnings
  stability, and growth. High quality + cheap valuation = real value;
  low quality + cheap valuation = value trap. Use as a multiplier on
  ``brain.valuation`` signals.

* **Earnings momentum** — direction and acceleration of EPS growth. One
  of the most well-documented anomalies in the equity literature
  ("post-earnings-announcement drift"). Returns a tag in
  ``{ACCELERATING, STEADY, DECELERATING, EROSION, INSUFFICIENT_DATA}``.

Both engines read from ``data/fundamentals/{SYM}.parquet`` written by
``connectors/yfinance_fundamentals.py``.

Quality score components and weights
====================================

============================  ======  ==============================
Component                     Weight  How it's scored
============================  ======  ==============================
Profitability (ROE)           30 %    >25% → 100, <0% → 0, linear
Leverage (Debt / Equity)      25 %    <0.5 → 100, >2.5 → 0, inverted
Earnings stability (5y CV)    20 %    <0.3 → 100, >1.0 → 0, inverted
Revenue growth (3y CAGR)      15 %    >15% → 100, <-5% → 0, linear
EPS growth (3y CAGR)          10 %    >15% → 100, <-5% → 0, linear
============================  ======  ==============================

For sectors where leverage is structurally high (Banking — by definition
debt-funded; Power — utility leverage), the leverage component is
re-anchored against sector-typical levels rather than the universe.

Quality bands
=============

* HIGH: score ≥ 70
* MEDIUM: 50 ≤ score < 70
* LOW: 30 ≤ score < 50
* JUNK: score < 30  (do NOT honour value signals on these names)
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Optional

from config.universe import sector_of, symbols as universe_symbols
from connectors.yfinance_fundamentals import load_latest, load_universe


# --------------------------------------------------------- score helpers
def _linear_score(value: float | None,
                   low: float, high: float,
                   inverted: bool = False) -> float | None:
    """Linear-clip score in [0, 100].

    ``inverted=True`` flips the mapping: small ``value`` → high score.
    """
    if value is None or (isinstance(value, float)
                          and (math.isnan(value) or math.isinf(value))):
        return None
    if inverted:
        # high value at `low` end (small numbers good)
        if value <= low:
            return 100.0
        if value >= high:
            return 0.0
        return round(100.0 * (high - value) / (high - low), 1)
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    return round(100.0 * (value - low) / (high - low), 1)


def _cagr(start: float, end: float, years: int) -> float | None:
    """Compound annual growth rate. ``None`` on invalid inputs.

    Note: ``end`` is the more recent year. Negative ``start`` makes CAGR
    undefined; we coerce that to ``None`` rather than returning a noisy
    number.
    """
    if not start or not end or years <= 0:
        return None
    if start <= 0:
        return None
    try:
        return round((end / start) ** (1 / years) - 1, 4)
    except Exception:
        return None


def _coef_var(values: list[float]) -> float | None:
    """Coefficient of variation: stdev / |mean|.

    Lower = more stable. ``None`` if mean is zero or list too short.
    """
    if not values or len(values) < 3:
        return None
    try:
        m = sum(values) / len(values)
        if m == 0:
            return None
        s = statistics.pstdev(values)
        return round(abs(s / m), 4)
    except Exception:
        return None


# ------------------------------------------------------ leverage handling
# Sectors where high D/E is structurally normal (banks intermediate
# deposits; utilities use project debt). For these, we re-anchor the
# scoring band so they don't all look junk.
LEVERAGE_BANDS = {
    # sector              -> (good_threshold, bad_threshold)
    "Banking":              (5.0, 12.0),   # banks: equity x10 in assets
    "Power":                (1.5, 4.0),
    "OMC/Refining":         (1.0, 3.0),
    "Conglomerate/Chem":    (1.0, 3.0),
    "_default":             (0.5, 2.5),
}


def _leverage_score(debt: float | None, equity: float | None,
                     sector: str | None) -> float | None:
    """Score the debt/equity ratio with sector-aware thresholds."""
    if debt is None or not equity or equity <= 0:
        return None
    de = debt / equity
    band = LEVERAGE_BANDS.get(sector or "", LEVERAGE_BANDS["_default"])
    return _linear_score(de, low=band[0], high=band[1], inverted=True)


# ---------------------------------------------------------- main scorer
def quality_score(symbol: str,
                   fb: Optional[dict] = None) -> dict:
    """Compute the composite quality score for one ticker.

    Returns
    -------
    dict
        ``{symbol, sector, quality_score (0-100), band, components,
        warnings, as_of_fundamentals}`` where ``components`` carries each
        sub-score and the raw ratio so the UI can show ``ROE = 18.4%
        (78/100 score, 30% weight)``.
    """
    fb = fb or load_latest(symbol)
    sec = sector_of(symbol)
    if not fb:
        return {"symbol": symbol, "sector": sec,
                "error": "no fundamentals cached",
                "quality_score": None, "band": "UNKNOWN"}

    eps_ttm = fb.get("eps_ttm")
    bvps = fb.get("book_value_per_share")
    shares = fb.get("shares_outstanding")
    eq = fb.get("total_equity_pkr")
    dt = fb.get("total_debt_pkr")
    rev_5y = fb.get("revenue_5y") or []
    ni_5y = fb.get("net_income_5y") or []
    eps_5y = fb.get("eps_5y") or []

    warnings: list[str] = []

    # -- Profitability: ROE
    # Prefer net_income / equity (raw filings). Fall back to EPS / BVPS.
    roe = None
    if ni_5y and eq and eq > 0:
        roe = ni_5y[0] / eq
    elif eps_ttm is not None and bvps and bvps > 0:
        roe = eps_ttm / bvps
    roe_score = _linear_score(
        (roe * 100 if roe is not None else None),
        low=0, high=25,
    )

    # -- Leverage
    lev_score = _leverage_score(dt, eq, sec)
    if lev_score is None and sec not in ("Banking",):
        warnings.append("debt/equity unavailable")

    # -- Earnings stability (low CV = stable)
    eps_cv = _coef_var(eps_5y)
    stab_score = _linear_score(eps_cv, low=0.3, high=1.0, inverted=True)
    if stab_score is None and len(eps_5y) < 3:
        warnings.append("EPS history < 3y")

    # -- Revenue growth (3y CAGR; rev_5y is newest-first)
    rev_3y_cagr = None
    if len(rev_5y) >= 4:
        rev_3y_cagr = _cagr(rev_5y[3], rev_5y[0], 3)
    elif len(rev_5y) >= 2:
        rev_3y_cagr = _cagr(rev_5y[-1], rev_5y[0], len(rev_5y) - 1)
    rev_score = _linear_score(
        (rev_3y_cagr * 100 if rev_3y_cagr is not None else None),
        low=-5, high=15,
    )

    # -- EPS growth (3y CAGR)
    eps_3y_cagr = None
    if len(eps_5y) >= 4:
        eps_3y_cagr = _cagr(eps_5y[3], eps_5y[0], 3)
    elif len(eps_5y) >= 2:
        eps_3y_cagr = _cagr(eps_5y[-1], eps_5y[0], len(eps_5y) - 1)
    eps_score = _linear_score(
        (eps_3y_cagr * 100 if eps_3y_cagr is not None else None),
        low=-5, high=15,
    )

    # ------------- weighted composite (re-normalize over what's available)
    weights = {"profitability": 0.30,
                "leverage":      0.25,
                "stability":     0.20,
                "rev_growth":    0.15,
                "eps_growth":    0.10}
    sub_scores = {"profitability": roe_score,
                   "leverage":      lev_score,
                   "stability":     stab_score,
                   "rev_growth":    rev_score,
                   "eps_growth":    eps_score}
    weighted: list[tuple[float, float]] = []
    for k, w in weights.items():
        v = sub_scores[k]
        if v is not None:
            weighted.append((w, v))
    if not weighted:
        composite = None
    else:
        wsum = sum(w for w, _ in weighted)
        composite = round(sum(w * v for w, v in weighted) / wsum, 1)

    # ------------- band
    if composite is None:
        band = "UNKNOWN"
    elif composite >= 70:
        band = "HIGH"
    elif composite >= 50:
        band = "MEDIUM"
    elif composite >= 30:
        band = "LOW"
    else:
        band = "JUNK"

    components = {
        "profitability": {
            "score": roe_score,
            "weight_pct": int(weights["profitability"] * 100),
            "metric": "ROE",
            "value": (round(roe * 100, 2)
                       if roe is not None else None),
            "unit": "%",
        },
        "leverage": {
            "score": lev_score,
            "weight_pct": int(weights["leverage"] * 100),
            "metric": "Debt / Equity",
            "value": (round(dt / eq, 2)
                       if (dt is not None and eq) else None),
            "unit": "ratio",
            "sector_band": LEVERAGE_BANDS.get(
                sec or "", LEVERAGE_BANDS["_default"]),
        },
        "stability": {
            "score": stab_score,
            "weight_pct": int(weights["stability"] * 100),
            "metric": "EPS coefficient of variation (5y)",
            "value": eps_cv,
            "unit": "CV",
        },
        "rev_growth": {
            "score": rev_score,
            "weight_pct": int(weights["rev_growth"] * 100),
            "metric": "Revenue 3y CAGR",
            "value": (round(rev_3y_cagr * 100, 2)
                       if rev_3y_cagr is not None else None),
            "unit": "%",
        },
        "eps_growth": {
            "score": eps_score,
            "weight_pct": int(weights["eps_growth"] * 100),
            "metric": "EPS 3y CAGR",
            "value": (round(eps_3y_cagr * 100, 2)
                       if eps_3y_cagr is not None else None),
            "unit": "%",
        },
    }

    return {
        "symbol": symbol,
        "sector": sec,
        "quality_score": composite,
        "band": band,
        "components": components,
        "warnings": warnings,
        "as_of_fundamentals": fb.get("as_of_utc"),
    }


def universe_quality_book() -> dict:
    """Compute quality scores for every universe ticker. Sorted desc."""
    books = load_universe()
    rows = [quality_score(s, fb=books.get(s)) for s in universe_symbols()]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("band", "UNKNOWN")] = (
            counts.get(r.get("band", "UNKNOWN"), 0) + 1
        )
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.get("quality_score") or -1),
        reverse=True,
    )
    return {"n_symbols": len(rows), "band_counts": counts, "rows": rows_sorted}


# ==========================================================================
# Earnings momentum
# ==========================================================================
def earnings_momentum(symbol: str, fb: Optional[dict] = None) -> dict:
    """EPS direction and acceleration flag.

    Reads ``eps_5y`` (newest-first list of 4-5 annual EPS values), computes:

    * ``yoy_growth_pct``        — most recent year vs prior year
    * ``prior_yoy_growth_pct``  — prior year vs the year before that
    * ``cagr_3y_pct``           — 3-year compound growth
    * ``acceleration``          — yoy − prior_yoy (in pct points)
    * ``flag`` ∈ {ACCELERATING, STEADY, DECELERATING, EROSION,
                  RECOVERING, INSUFFICIENT_DATA}

    Decision rules
    --------------
    * ACCELERATING   — yoy > 5 % AND acceleration > 5 pp
    * RECOVERING     — yoy > 5 % AND prior_yoy < 0  (out of a slump)
    * STEADY         — |yoy| ≤ 5 % AND |acceleration| ≤ 5 pp
    * DECELERATING   — yoy still positive but acceleration < −5 pp
    * EROSION        — yoy < −5 % (earnings shrinking)
    """
    fb = fb or load_latest(symbol)
    sec = sector_of(symbol)
    if not fb:
        return {"symbol": symbol, "sector": sec,
                "error": "no fundamentals cached",
                "flag": "INSUFFICIENT_DATA"}

    eps_5y = fb.get("eps_5y") or []
    if len(eps_5y) < 3:
        return {"symbol": symbol, "sector": sec,
                "flag": "INSUFFICIENT_DATA",
                "eps_5y": eps_5y,
                "error": f"need ≥3 years EPS history, have {len(eps_5y)}"}

    e0, e1, e2 = eps_5y[0], eps_5y[1], eps_5y[2]
    yoy = ((e0 - e1) / abs(e1) * 100) if e1 not in (0, None) else None
    prior_yoy = ((e1 - e2) / abs(e2) * 100) if e2 not in (0, None) else None
    # Guard against meaningless % when both years are deeply negative
    # (e.g. EPCL going from -3 to -73 PKR → -2300% is mathematically right
    # but useless as a momentum signal). Cap at +/-500%.
    def _cap(x):
        return None if x is None else max(-500.0, min(500.0, x))
    yoy = _cap(yoy)
    prior_yoy = _cap(prior_yoy)
    acceleration = (yoy - prior_yoy
                     if (yoy is not None and prior_yoy is not None) else None)

    cagr_3y = None
    if len(eps_5y) >= 4 and eps_5y[3]:
        cagr_3y = _cagr(eps_5y[3], e0, 3)
        if cagr_3y is not None:
            cagr_3y = round(cagr_3y * 100, 2)

    # Decision tree
    if yoy is None:
        flag = "INSUFFICIENT_DATA"
    elif yoy < -5:
        flag = "EROSION"
    elif acceleration is None:
        flag = "STEADY" if abs(yoy) <= 5 else (
            "ACCELERATING" if yoy > 5 else "DECELERATING")
    elif yoy > 5 and prior_yoy is not None and prior_yoy < 0:
        flag = "RECOVERING"
    elif yoy > 5 and acceleration > 5:
        flag = "ACCELERATING"
    elif acceleration < -5 and yoy >= 0:
        flag = "DECELERATING"
    elif abs(yoy) <= 5 and abs(acceleration) <= 5:
        flag = "STEADY"
    else:
        flag = "STEADY"

    return {
        "symbol": symbol,
        "sector": sec,
        "flag": flag,
        "yoy_growth_pct":      (round(yoy, 2) if yoy is not None else None),
        "prior_yoy_growth_pct": (round(prior_yoy, 2)
                                  if prior_yoy is not None else None),
        "acceleration_pp":     (round(acceleration, 2)
                                if acceleration is not None else None),
        "cagr_3y_pct":         cagr_3y,
        "eps_5y":              eps_5y,
        "as_of_fundamentals":  fb.get("as_of_utc"),
    }


def universe_earnings_momentum() -> dict:
    """Earnings-momentum flags for every universe ticker."""
    books = load_universe()
    rows = [earnings_momentum(s, fb=books.get(s))
             for s in universe_symbols()]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("flag", "INSUFFICIENT_DATA")] = (
            counts.get(r.get("flag", "INSUFFICIENT_DATA"), 0) + 1
        )
    return {"n_symbols": len(rows), "flag_counts": counts, "rows": rows}


if __name__ == "__main__":  # pragma: no cover
    print("=== QUALITY ===")
    qb = universe_quality_book()
    print(f"bands: {qb['band_counts']}")
    print(f"{'Sym':<6} {'Sec':<22} {'Score':>6} {'Band':<7}  ROE / D/E / CV / RevG / EPSG")
    for r in qb["rows"]:
        if r.get("error"):
            print(f"  {r['symbol']:<6} {r.get('sector','?')[:22]:<22} "
                  f"     - UNKNOWN  ({r['error']})")
            continue
        c = r["components"]
        print(f"  {r['symbol']:<6} {r.get('sector','?')[:22]:<22} "
              f"{(r['quality_score'] or 0):>6.1f} {r['band']:<7}  "
              f"{c['profitability']['value']!s:>6} / "
              f"{c['leverage']['value']!s:>5} / "
              f"{c['stability']['value']!s:>5} / "
              f"{c['rev_growth']['value']!s:>5} / "
              f"{c['eps_growth']['value']!s:>5}")

    print("\n=== EARNINGS MOMENTUM ===")
    em = universe_earnings_momentum()
    print(f"flags: {em['flag_counts']}")
    print(f"{'Sym':<6} {'Flag':<17} {'YoY%':>8} {'PrYoY%':>8} {'Acc(pp)':>8} {'3yCAGR%':>9}")
    for r in em["rows"]:
        if r.get("error"):
            continue
        print(f"  {r['symbol']:<6} {r['flag']:<17} "
              f"{(r.get('yoy_growth_pct') or 0):>+7.2f}% "
              f"{(r.get('prior_yoy_growth_pct') or 0):>+7.2f}% "
              f"{(r.get('acceleration_pp') or 0):>+7.2f}  "
              f"{(r.get('cagr_3y_pct') or 0):>+8.2f}%")
