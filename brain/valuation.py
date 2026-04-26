"""Sector-aware fair-value model for the PSX 15-stock universe.

Every method returns a *fair value* in PKR per share. We then compare to the
fresh PSX close from ``connectors/psx_historical.py`` to derive::

    upside_pct          = fair_value / current_price - 1
    margin_of_safety_pct = upside_pct (positive = market trades at a discount)
    value_signal        = BUY_VALUE | FAIR | SELL_VALUE | NO_SIGNAL

Sector rules — chosen for PSX market structure
==============================================

============================  =================================================
Sector                        Method
============================  =================================================
Banking (FABL, MCB, MEBL)     DDM primary, P/B secondary. Banks are valued on
                              dividends and book value, not earnings (which
                              are lumpy due to provisioning).
Oil & Gas E&P                 P/B primary, P/E secondary. Asset-rich;
(OGDC, PPL, POL)              reserves carried at book.
Cement (MLCF, FCCL, KOHC)     P/E primary using 3-yr average EPS (cycle-adj).
                              Cement is cyclical so single-year P/E is noisy.
OMC/Refining (APL, PSO)       50/50 blend of P/E and P/B. Volatile margins.
Power (HUBC)                  DDM primary (regulated utility).
Pharma (SEARL)                P/E only, with quality filter.
Conglomerate/Chem (EPCL)      P/B only (asset-heavy, lumpy earnings).
Misc (PABC)                   50/50 blend P/E + P/B.
============================  =================================================

Margin-of-safety thresholds
===========================

* ``BUY_VALUE`` — current price ≤ 75 % of fair value (≥ 25 % upside).
* ``SELL_VALUE`` — current price ≥ 110 % of fair value (≤ −10 % upside).
* ``FAIR`` — anything in between.
* ``NO_SIGNAL`` — quality filter killed the estimate (negative EPS, missing
  data, P/E > 50, etc.).

Why these thresholds? PSX is structurally undervalued vs developed markets
because of capital controls, so a 25 % discount filter (vs the typical 30 %
Graham margin) avoids killing every buy signal. The −10 % sell trigger is
asymmetric — we let winners run a bit before fading them.

Required-return assumptions for DDM
===================================

* PSX banks:    r = 0.16 (KIBOR 11 % + 5 % equity premium).
* PSX utility:  r = 0.15 (slightly lower premium given regulated cash flows).
* g = min(5y dividend CAGR, 0.08) — capped because terminal g ≥ r breaks DDM.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from config.universe import sector_of, symbols as universe_symbols
from connectors.yfinance_fundamentals import load_latest, load_universe


# --------------------------------------------------------------- thresholds
BUY_DISCOUNT_PCT = 25.0      # need ≥25% upside to call BUY_VALUE
SELL_PREMIUM_PCT = -10.0     # need ≤-10% upside to call SELL_VALUE

DDM_REQUIRED_RETURN = {
    "Banking": 0.16,
    "Power": 0.15,
}
DDM_MAX_GROWTH = 0.08

PE_HARD_CAP = 60.0           # ignore P/E above this; usually data error
EPS_HISTORY_MIN_YEARS = 3    # need ≥3 years for cycle-adjusted EPS


# -------------------------------------------------------------- sector medians
def _sector_medians(books: dict[str, dict],
                    prices: dict[str, float]) -> dict[str, dict[str, float]]:
    """Compute sector-median P/E and P/B from current prices and the book.

    Returns ``{sector: {"pe_med": x, "pb_med": y, "n": z}}``. Sectors with
    fewer than 2 valid samples fall back to the universe-wide median, so
    single-stock sectors (Power, Pharma, Misc, Conglomerate/Chem) still get
    a usable multiple.
    """
    by_sector: dict[str, dict[str, list[float]]] = {}
    universe_pes: list[float] = []
    universe_pbs: list[float] = []

    for sym, fb in books.items():
        sec = sector_of(sym)
        if not sec:
            continue
        bucket = by_sector.setdefault(sec, {"pe": [], "pb": []})

        eps = fb.get("eps_ttm")
        bvps = fb.get("book_value_per_share")
        px = prices.get(sym)
        if not px or px <= 0:
            continue

        if eps and eps > 0:
            pe = px / eps
            if 0 < pe < PE_HARD_CAP:
                bucket["pe"].append(pe)
                universe_pes.append(pe)

        if bvps and bvps > 0:
            pb = px / bvps
            if 0 < pb < 20:
                bucket["pb"].append(pb)
                universe_pbs.append(pb)

    out: dict[str, dict[str, float]] = {}
    uni_pe = statistics.median(universe_pes) if universe_pes else 6.0
    uni_pb = statistics.median(universe_pbs) if universe_pbs else 1.0

    for sec in {sector_of(s) for s in books if sector_of(s)}:
        b = by_sector.get(sec, {"pe": [], "pb": []})
        pe_med = (statistics.median(b["pe"]) if len(b["pe"]) >= 2
                  else uni_pe)
        pb_med = (statistics.median(b["pb"]) if len(b["pb"]) >= 2
                  else uni_pb)
        out[sec] = {"pe_med": round(pe_med, 2),
                    "pb_med": round(pb_med, 3),
                    "n": max(len(b["pe"]), len(b["pb"]))}
    out["__universe__"] = {"pe_med": round(uni_pe, 2),
                           "pb_med": round(uni_pb, 3),
                           "n": max(len(universe_pes), len(universe_pbs))}
    return out


# ------------------------------------------------------------- helpers / DDM
def _avg_3y_eps(fb: dict) -> float | None:
    """Cycle-adjusted EPS: mean of the latest 3 years from ``eps_5y``.

    Falls back to ``eps_ttm`` if we don't have 3 years of history.
    """
    eps_hist = fb.get("eps_5y") or []
    if len(eps_hist) >= EPS_HISTORY_MIN_YEARS:
        return round(sum(eps_hist[:EPS_HISTORY_MIN_YEARS])
                     / EPS_HISTORY_MIN_YEARS, 4)
    return fb.get("eps_ttm")


def _div_growth_capped(fb: dict) -> float:
    """Estimate dividend growth, capped at ``DDM_MAX_GROWTH``.

    Compares 5-yr average dividend per share to TTM dividend per share. If
    either is missing or growth is non-positive we return 0.0 so DDM
    degenerates to the classic ``D/r`` perpetuity.
    """
    d_ttm = fb.get("dividend_ttm") or 0
    d_5y = fb.get("dividend_5y_avg") or 0
    if d_ttm <= 0 or d_5y <= 0:
        return 0.0
    # 5y CAGR is sketchy with only two anchor points; use simple ratio
    # (TTM / 5y avg)^(1/4) as a rough annualization.
    try:
        g = (d_ttm / d_5y) ** (1 / 4) - 1
    except Exception:
        return 0.0
    return max(0.0, min(g, DDM_MAX_GROWTH))


def _ddm_value(fb: dict, sector: str) -> tuple[float | None, dict]:
    """Gordon Growth DDM: ``V = D₁ / (r - g)``.

    Returns ``(fair_value, components)``. ``fair_value`` is ``None`` if we
    can't compute (no dividend, missing required return, or g ≥ r).
    """
    d_ttm = fb.get("dividend_ttm") or 0
    if d_ttm <= 0:
        return None, {"reason": "no TTM dividend"}
    r = DDM_REQUIRED_RETURN.get(sector)
    if r is None:
        return None, {"reason": f"no DDM required return for {sector}"}
    g = _div_growth_capped(fb)
    if g >= r:
        g = r - 0.02  # safety: enforce r > g
    d1 = d_ttm * (1 + g)
    v = d1 / (r - g)
    return round(v, 2), {
        "method": "DDM",
        "D_ttm": d_ttm,
        "g": round(g, 4),
        "r": r,
        "D1": round(d1, 4),
        "formula": f"D1 / (r - g) = {d1:.2f} / ({r:.2f} - {g:.4f})",
    }


def _multiples_value(fb: dict, sector_med: dict[str, float],
                     px: float) -> dict:
    """P/E and P/B fair-value derivations.

    Both are computed when inputs are valid; the caller decides which to use
    or how to blend per the sector rule. Each component carries its own
    quality flag so the final signal can downgrade conviction.
    """
    out: dict[str, Any] = {}

    eps_3y = _avg_3y_eps(fb)
    bvps = fb.get("book_value_per_share")

    if eps_3y and eps_3y > 0:
        pe_v = round(eps_3y * sector_med["pe_med"], 2)
        out["pe"] = {
            "value": pe_v,
            "method": "P/E × 3y-avg EPS",
            "eps_3y": eps_3y,
            "sector_pe": sector_med["pe_med"],
            "formula": f"{eps_3y:.2f} × {sector_med['pe_med']:.2f}",
            "quality": "ok",
        }
    elif fb.get("eps_ttm") and fb["eps_ttm"] > 0:
        eps = fb["eps_ttm"]
        pe_v = round(eps * sector_med["pe_med"], 2)
        out["pe"] = {
            "value": pe_v,
            "method": "P/E × TTM EPS (no 3y history)",
            "eps_ttm": eps,
            "sector_pe": sector_med["pe_med"],
            "formula": f"{eps:.2f} × {sector_med['pe_med']:.2f}",
            "quality": "limited-history",
        }
    else:
        out["pe"] = {"value": None, "quality": "no-positive-eps",
                     "method": "P/E"}

    if bvps and bvps > 0:
        pb_v = round(bvps * sector_med["pb_med"], 2)
        out["pb"] = {
            "value": pb_v,
            "method": "P/B × BVPS",
            "bvps": bvps,
            "sector_pb": sector_med["pb_med"],
            "formula": f"{bvps:.2f} × {sector_med['pb_med']:.3f}",
            "quality": "ok",
        }
    else:
        out["pb"] = {"value": None, "quality": "no-bvps", "method": "P/B"}

    return out


def _graham_value(fb: dict) -> float | None:
    """Graham number sanity check: ``sqrt(22.5 × EPS × BVPS)``.

    Used as a tertiary signal — capped at PE_HARD_CAP × EPS to avoid
    bubble multiples.
    """
    eps = _avg_3y_eps(fb) or fb.get("eps_ttm")
    bvps = fb.get("book_value_per_share")
    if not eps or eps <= 0 or not bvps or bvps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bvps), 2)


# ------------------------------------------------------------- main entry
def _signal_from_upside(upside_pct: float | None) -> str:
    if upside_pct is None:
        return "NO_SIGNAL"
    if upside_pct >= BUY_DISCOUNT_PCT:
        return "BUY_VALUE"
    if upside_pct <= SELL_PREMIUM_PCT:
        return "SELL_VALUE"
    return "FAIR"


def value_signal(symbol: str,
                 current_price: float | None = None,
                 books: dict[str, dict] | None = None,
                 prices: dict[str, float] | None = None) -> dict:
    """Sector-aware value signal for one ticker.

    Parameters
    ----------
    symbol
        PSX ticker.
    current_price
        Latest close in PKR. If ``None`` we read it via
        ``ui.tools.get_price``.
    books, prices
        Optional pre-loaded fundamentals dict and price map. Pass these in
        when scoring the entire universe at once to avoid 15× redundant
        I/O.

    Returns
    -------
    dict
        ``{"symbol", "sector", "current_price", "fair_value",
           "upside_pct", "signal", "method", "components", "warnings",
           "as_of_fundamentals"}``.

        ``signal`` ∈ ``{BUY_VALUE, FAIR, SELL_VALUE, NO_SIGNAL}``.
    """
    sec = sector_of(symbol)
    if not sec:
        return {"symbol": symbol, "error": "unknown symbol", "signal": "NO_SIGNAL"}

    if books is None:
        books = load_universe()
    fb = books.get(symbol) or load_latest(symbol) or {}
    if not fb or not fb.get("ok", True):
        return {"symbol": symbol, "sector": sec,
                "error": "no fundamentals cached — run "
                         "scripts/refresh_fundamentals.py first",
                "signal": "NO_SIGNAL"}

    if current_price is None:
        try:
            from ui import tools as _t
            current_price = (_t.get_price(symbol) or {}).get("close_pkr")
        except Exception:
            current_price = None
    if not current_price or current_price <= 0:
        return {"symbol": symbol, "sector": sec,
                "error": "no current price",
                "signal": "NO_SIGNAL"}

    # Build universe price map only if we have to (need it for sector medians)
    if prices is None:
        try:
            from ui import tools as _t
            prices = {}
            for s in universe_symbols():
                p = (_t.get_price(s) or {}).get("close_pkr")
                if p:
                    prices[s] = float(p)
        except Exception:
            prices = {symbol: current_price}

    medians = _sector_medians(books, prices)
    sec_med = medians.get(sec) or medians["__universe__"]

    components = _multiples_value(fb, sec_med, current_price)
    components["graham"] = {"value": _graham_value(fb),
                            "method": "sqrt(22.5 × EPS × BVPS)"}
    ddm_v, ddm_meta = _ddm_value(fb, sec)
    components["ddm"] = {**ddm_meta, "value": ddm_v}

    warnings: list[str] = []

    # ---- sector rule: pick fair value(s) and combine
    method_used: str
    fair: float | None = None
    eps_ttm = fb.get("eps_ttm") or 0

    if sec == "Banking":
        # DDM primary, P/B secondary (50/50 if both available)
        pb_v = components["pb"].get("value")
        if ddm_v and pb_v:
            fair = round((ddm_v + pb_v) / 2, 2)
            method_used = "Banking: 50% DDM + 50% P/B"
        elif ddm_v:
            fair, method_used = ddm_v, "Banking: DDM only (no BVPS)"
        elif pb_v:
            fair, method_used = pb_v, "Banking: P/B only (no dividend)"
            warnings.append("DDM unavailable — using P/B alone")
        else:
            method_used = "Banking: failed (no DDM, no P/B)"

    elif sec == "Oil & Gas E&P":
        pb_v = components["pb"].get("value")
        pe_v = components["pe"].get("value")
        if pb_v and pe_v:
            fair = round(0.6 * pb_v + 0.4 * pe_v, 2)
            method_used = "E&P: 60% P/B + 40% P/E"
        elif pb_v:
            fair, method_used = pb_v, "E&P: P/B only"
        elif pe_v:
            fair, method_used = pe_v, "E&P: P/E only (no BVPS)"
        else:
            method_used = "E&P: failed (no P/B, no P/E)"
            warnings.append("Both EPS and BVPS missing")

    elif sec == "Cement":
        pe_v = components["pe"].get("value")
        if pe_v and components["pe"].get("quality") == "ok":
            fair, method_used = pe_v, "Cement: P/E × 3y-avg EPS"
        elif pe_v:
            fair, method_used = pe_v, "Cement: P/E (TTM only — cycle risk)"
            warnings.append("3y EPS history missing — cyclical risk under-priced")
        else:
            pb_v = components["pb"].get("value")
            if pb_v:
                fair, method_used = pb_v, "Cement: fallback P/B (loss-making)"
                warnings.append("Negative TTM EPS — using P/B floor")
            else:
                method_used = "Cement: failed"

    elif sec == "OMC/Refining":
        pe_v = components["pe"].get("value")
        pb_v = components["pb"].get("value")
        if pe_v and pb_v:
            fair = round((pe_v + pb_v) / 2, 2)
            method_used = "OMC: 50% P/E + 50% P/B"
        elif pe_v:
            fair, method_used = pe_v, "OMC: P/E only"
        elif pb_v:
            fair, method_used = pb_v, "OMC: P/B only"
            warnings.append("EPS missing — P/B floor only")
        else:
            method_used = "OMC: failed"

    elif sec == "Power":
        # HUBC: DDM-first like a utility
        if ddm_v:
            pb_v = components["pb"].get("value")
            if pb_v:
                fair = round(0.7 * ddm_v + 0.3 * pb_v, 2)
                method_used = "Power: 70% DDM + 30% P/B"
            else:
                fair, method_used = ddm_v, "Power: DDM only"
        else:
            pb_v = components["pb"].get("value")
            if pb_v:
                fair, method_used = pb_v, "Power: P/B fallback"
                warnings.append("DDM unavailable")
            else:
                method_used = "Power: failed"

    elif sec == "Pharma":
        pe_v = components["pe"].get("value")
        if pe_v and eps_ttm > 0:
            fair, method_used = pe_v, "Pharma: P/E × 3y-avg EPS"
        else:
            method_used = "Pharma: NO_SIGNAL (negative or missing EPS)"
            warnings.append("Pharma valuation requires positive EPS")

    elif sec == "Conglomerate/Chem":
        pb_v = components["pb"].get("value")
        if pb_v:
            fair, method_used = pb_v, "Conglomerate: P/B (lumpy earnings)"
        else:
            method_used = "Conglomerate: failed (no BVPS)"

    else:  # Misc + safety net
        pe_v = components["pe"].get("value")
        pb_v = components["pb"].get("value")
        if pe_v and pb_v:
            fair = round((pe_v + pb_v) / 2, 2)
            method_used = "Misc: 50% P/E + 50% P/B"
        elif pe_v:
            fair, method_used = pe_v, "Misc: P/E only"
        elif pb_v:
            fair, method_used = pb_v, "Misc: P/B only"
        else:
            method_used = "Misc: failed"

    # ------------------------------------------------------------- quality
    upside_pct = (round((fair / current_price - 1) * 100, 2)
                  if fair else None)
    signal = _signal_from_upside(upside_pct)

    if eps_ttm < 0 and signal == "BUY_VALUE":
        # Loss-making → don't issue a hard BUY even if fair value is high
        warnings.append("TTM EPS negative — downgrading BUY_VALUE → FAIR")
        signal = "FAIR"

    # Sanity: if fair value > 4× book, the multiple is probably wrong
    bvps = fb.get("book_value_per_share")
    if fair and bvps and bvps > 0 and fair > 4 * bvps:
        warnings.append(
            f"Fair value {fair:.0f} > 4× book {bvps:.0f} — likely overshoot"
        )

    # ----------------------------------------------------------- confidence
    # HIGH: clean inputs, normal-range upside, sector has peers.
    # MED:  one quality flag OR universe-fallback medians OR upside 40-60%.
    # LOW:  extreme upside (>60%), loss-making, or multiple warnings.
    has_peers = sec_med.get("n", 0) >= 2 and sec != "Pharma"
    abs_up = abs(upside_pct) if upside_pct is not None else 0
    if upside_pct is None:
        confidence = "LOW"
    elif abs_up > 60 or eps_ttm < 0 or len(warnings) >= 2:
        confidence = "LOW"
    elif abs_up > 40 or len(warnings) == 1 or not has_peers:
        confidence = "MED"
    else:
        confidence = "HIGH"

    return {
        "symbol": symbol,
        "sector": sec,
        "current_price": round(float(current_price), 2),
        "fair_value": fair,
        "upside_pct": upside_pct,
        "signal": signal,
        "confidence": confidence,
        "method": method_used,
        "components": components,
        "sector_medians": sec_med,
        "warnings": warnings,
        "as_of_fundamentals": fb.get("as_of_utc"),
    }


def universe_value_book() -> dict:
    """Compute a value signal for every symbol in the universe.

    Reuses a single fundamentals dict and a single price map so the call is
    O(N) yfinance reads, not O(N²).
    """
    books = load_universe()
    prices: dict[str, float] = {}
    try:
        from ui import tools as _t
        for s in universe_symbols():
            p = (_t.get_price(s) or {}).get("close_pkr")
            if p:
                prices[s] = float(p)
    except Exception:
        pass

    rows: list[dict] = []
    for sym in universe_symbols():
        rec = value_signal(sym, current_price=prices.get(sym),
                           books=books, prices=prices)
        rows.append(rec)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("signal", "NO_SIGNAL")] = (
            counts.get(r.get("signal", "NO_SIGNAL"), 0) + 1
        )
    rows_sorted = sorted(
        rows,
        key=lambda r: (r.get("upside_pct") if r.get("upside_pct") is not None
                        else -999),
        reverse=True,
    )
    return {
        "n_symbols": len(rows),
        "signal_counts": counts,
        "rows": rows_sorted,
    }


if __name__ == "__main__":  # pragma: no cover
    import json as _j
    book = universe_value_book()
    print(_j.dumps(book["signal_counts"], indent=2))
    print(f"\n{'Sym':<6} {'Sec':<22} {'Px':>9} {'Fair':>9} "
          f"{'Up%':>8} {'Signal':<11} Method")
    print("-" * 110)
    for r in book["rows"]:
        if "error" in r:
            print(f"  {r['symbol']:<6} ERROR: {r['error']}")
            continue
        print(f"  {r['symbol']:<6} {r.get('sector','')[:22]:<22} "
              f"{r['current_price']:>9.2f} "
              f"{(r.get('fair_value') or 0):>9.2f} "
              f"{(r.get('upside_pct') or 0):>+7.1f}% "
              f"{r['signal']:<11} {r.get('method','')}")
