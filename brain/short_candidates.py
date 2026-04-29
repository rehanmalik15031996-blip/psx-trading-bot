"""Composite short-candidate scorer.

Inverts the bot's existing bullish pipeline to surface stocks the
strategist thinks will go DOWN over the next ~5 sessions, ranked by
a transparent 0-100 ``short_score`` built from six bearish signal
buckets plus a regime adjustment.

This module is read-only: it consumes outputs the rest of the bot
already produces (verdict synthesizer, predictions log, scored
news, technical snapshot, macro impact, intraday circuit
breakers) and emits a ranked list. No new data sources, no new
LLM calls — same deterministic plumbing as the long side.

Score buckets (max 100)
-----------------------
  Verdict synthesizer (bearish lens score)             30
  5-day prediction expected return (if BEARISH)        25
  News sentiment over the trailing 7 days              15
  Technical breakdown (overbought + below 20-SMA, or
      strong negative 21d trend)                       15
  Macro headwind for sector                            10
  Intraday lower-circuit hit in last 5 sessions         5

Regime adjustment
-----------------
* RISK_OFF / KSE-100 below 20-SMA → no penalty (shorts are
  aligned).
* RISK_ON / KSE-100 in clean uptrend → conviction is downgraded one
  notch (HIGH→MEDIUM, MEDIUM→LOW). Shorting a bull market is the
  most common retail mistake; the bot flags it.

Concentration cap
-----------------
Mirrors the long side. If three or more candidates land in the
same sector, the lowest-scoring of them is downgraded to LOW
conviction with a ``concentration_warning`` so the analyst is not
piling six energy shorts on top of each other.

Eligibility hint
----------------
Every candidate carries the conservative
``config.short_eligibility.short_eligibility`` block. The UI
disclaimer makes clear the bot is NOT replacing broker-side
eligibility checks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --- helpers -----------------------------------------------------------------


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --- bucket scorers ----------------------------------------------------------


def _score_synth(verdict: dict | None) -> tuple[float, str]:
    """0-30 from the verdict synthesizer's bearish lean."""
    if not verdict:
        return 0.0, ""
    score = float(verdict.get("score") or 0)
    action = (verdict.get("action") or "").upper()
    if score >= 0:
        return 0.0, ""
    # Theoretical synth range is roughly -15 to +15. Map -15 to 30,
    # 0 to 0. Boost slightly for AVOID over TRIM.
    pts = _clamp((-score) / 15.0 * 30.0, 0.0, 30.0)
    if action == "AVOID":
        pts = min(30.0, pts + 3.0)
    note = (f"Synthesizer says {action or 'BEARISH'} "
            f"(composite score {int(score)})")
    return pts, note


def _score_prediction(pred: dict | None) -> tuple[float, str]:
    """0-25 from the 5-day expected return.

    `tools.get_todays_predictions` renames the raw mid forecast from
    ``expected_return_5d_mid_pct`` to ``expected_gross_5d_pct`` and
    drops the low / high band. We accept either key so the bucket
    works whether we are reading the prediction log directly or via
    the tools API.
    """
    if not pred:
        return 0.0, ""
    direction = (pred.get("direction") or "").upper()
    mid = pred.get("expected_gross_5d_pct")
    if mid is None:
        mid = pred.get("expected_return_5d_mid_pct")
    try:
        mid = float(mid or 0)
    except Exception:
        mid = 0.0
    if direction != "BEARISH" or mid >= 0:
        return 0.0, ""
    pts = _clamp((-mid) / 8.0 * 25.0, 0.0, 25.0)
    conv = pred.get("conviction") or "LOW"
    note = (f"5-day prediction: BEARISH, mid {mid:+.1f}% "
            f"({conv} conviction)")
    return pts, note


def _score_news(sym: str) -> tuple[float, str]:
    """0-15 from per-symbol scored news sentiment over the last 7 days."""
    try:
        from ui import tools
        sent = tools.get_scored_sentiment(symbol=sym, hours=7 * 24)
    except Exception:
        return 0.0, ""
    by_sym = (sent or {}).get("symbol") or {}
    score = by_sym.get("score")
    n = int(by_sym.get("n") or 0)
    if score is None or n == 0 or score >= 0:
        return 0.0, ""
    pts = _clamp((-float(score)) * 15.0, 0.0, 15.0)
    # Confidence floor: a single sharply-negative article shouldn't
    # earn the full 15 points. Scale linearly until we have at least
    # 4 articles in the window.
    if n < 4:
        pts *= n / 4.0
    return pts, (f"News sentiment last 7d: {score:+.2f} on n={n} "
                  f"articles")


def _score_technical(tech: dict | None) -> tuple[float, str]:
    """0-15 from the technical posture.

    Awards points for two distinct breakdown patterns:
      * Overbought (RSI > 65) AND price below the 20-SMA (a textbook
        rolling-over setup).
      * Or strong negative 21-day return (< -7%).
    """
    if not tech or "error" in tech:
        return 0.0, ""
    rsi = tech.get("rsi_14")
    ma = tech.get("moving_averages") or {}
    px_vs_sma20 = ma.get("px_vs_sma20_pct")
    mom = tech.get("momentum") or {}
    ret_21d = mom.get("21d_ret") or mom.get("ret_21d")
    pts = 0.0
    notes: list[str] = []

    if (rsi is not None and rsi > 65 and
            px_vs_sma20 is not None and px_vs_sma20 < 0):
        # Map RSI 65→5pts, RSI 80→12pts.
        pts += _clamp((rsi - 65) / 15.0 * 7.0 + 5.0, 5.0, 12.0)
        notes.append(f"Overbought RSI {rsi:.0f} + price "
                      f"{px_vs_sma20:+.1f}% vs 20-SMA")
    elif ret_21d is not None and ret_21d <= -0.07:
        # Clean breakdown: 7%+ drop in 21 days.
        pts += _clamp((-ret_21d - 0.07) / 0.13 * 8.0 + 5.0,
                       5.0, 13.0)
        notes.append(f"21-day return {ret_21d * 100:+.1f}% — "
                      "trend already broken")
    elif px_vs_sma20 is not None and px_vs_sma20 < -3.0:
        pts += 4.0
        notes.append(f"Price {px_vs_sma20:+.1f}% below 20-SMA")

    pts = min(pts, 15.0)
    return pts, "; ".join(notes)


def _score_macro_headwind(sym: str, sector: str) -> tuple[float, str]:
    """0-10 if the macro engine flags headwinds for this stock's sector."""
    try:
        from brain.macro_impact import compute_macro_impact
        mi = compute_macro_impact(universe=[sym])
    except Exception:
        return 0.0, ""
    by_sec = (mi.get("by_sector") or {}).get(sector or "", {})
    headwinds = by_sec.get("headwinds") or []
    if not headwinds:
        return 0.0, ""
    pts = _clamp(len(headwinds) * 4.0, 0.0, 10.0)
    return pts, (f"Macro headwind for {sector}: "
                  f"{headwinds[0] if headwinds else ''}")


def _score_intraday_pressure(sym: str) -> tuple[float, str]:
    """0-5 if this name has hit the lower circuit recently."""
    cb_path = PROJECT_ROOT / "data" / "intraday" / "circuit_breakers.parquet"
    if not cb_path.exists():
        return 0.0, ""
    try:
        import pandas as pd

        df = pd.read_parquet(cb_path)
        if df.empty:
            return 0.0, ""
        df["ts"] = pd.to_datetime(df["snapshot_at"], utc=True,
                                     errors="coerce")
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=5)
        recent = df[(df["ts"] >= cutoff)
                     & (df["symbol"] == sym)
                     & (df["direction"] == "lower")]
        if recent.empty:
            return 0.0, ""
        return 5.0, (f"Lower circuit hit in last "
                      f"{len(recent)} intraday snapshots")
    except Exception:
        return 0.0, ""


# --- regime ------------------------------------------------------------------


def _regime_adjustment() -> dict:
    """Return a hint describing whether the regime is hostile to shorts."""
    try:
        from ui import tools
        reg = tools.get_market_regime()
    except Exception:
        reg = {}
    name = (reg.get("regime") or "").upper()
    hostile = name in {"RISK_ON", "BULL", "STRONG_RISK_ON"}
    return {
        "regime":           name or "UNKNOWN",
        "shorts_aligned":   not hostile,
        "exposure_hint":    reg.get("exposure_multiplier", 1.0),
        "note": (
            "Regime favours shorts — KSE-100 is in a bearish or "
            "defensive posture, so the strategist's conviction is "
            "kept at face value."
            if not hostile else
            "Regime is RISK_ON / KSE-100 trending up. Shorts work "
            "AGAINST the broader market today; the strategist will "
            "downgrade every short candidate one conviction notch."
        ),
    }


# --- conviction & ranking ----------------------------------------------------


def _bucket_conviction(pts: float) -> str:
    """Map composite short_score to conviction.

    HIGH    >= 70    Strong, multi-signal bearish setup.
    MEDIUM  >= 45    A real short candidate — at least 2-3 buckets
                       firing.
    LOW     >= 10    "Watch" — one or two buckets are leaning bearish
                       but the thesis is thin. Surfaced so the analyst
                       can monitor names that may break down.
    (below 10 is filtered out before this function is called.)
    """
    if pts >= 70:
        return "HIGH"
    if pts >= 45:
        return "MEDIUM"
    return "LOW"


def _downgrade(conv: str) -> str:
    return {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}[conv]


def _apply_concentration_caps(rows: list[dict]) -> list[dict]:
    """Mirror of the long-side concentration cap.

    If three or more shorts land in the same sector, the
    lowest-scoring of them is forced to LOW conviction with a
    ``concentration_warning`` so the analyst doesn't pile six oil
    shorts on top of each other.
    """
    if not rows:
        return rows
    while True:
        by_sector: dict[str, list[dict]] = {}
        for r in rows:
            if (r.get("conviction") or "LOW").upper() != "LOW":
                by_sector.setdefault(r.get("sector") or "Other",
                                       []).append(r)
        offender = next(((s, p) for s, p in by_sector.items()
                          if len(p) >= 3), None)
        if offender is None:
            break
        sector, picks = offender
        weakest = sorted(picks, key=lambda r: r["short_score"])[0]
        weakest["conviction"] = "LOW"
        weakest["concentration_warning"] = (
            f"Sector '{sector}' already has {len(picks)} short "
            f"candidates; this is the weakest of them and was "
            f"capped to LOW conviction so the bot's recommendations "
            f"stay diversified."
        )
    return rows


# --- per-symbol levels -------------------------------------------------------


def _suggest_levels(price_now: float | None,
                       pred: dict | None) -> dict:
    """Suggest entry / stop / target for a short trade.

    Mirror of the long-side geometry:
      * entry slightly ABOVE the current quote (don't chase a
        already-falling print — wait for a bounce)
      * stop above entry (stop-out if the bounce continues)
      * target below entry, scaled by the predicted move
    """
    if price_now is None or price_now <= 0:
        return {}
    entry = round(price_now * 1.005, 2)         # +0.5% on a bounce
    stop = round(entry * 1.04, 2)               # -4% reversal
    expected_pct = -3.0
    pred_mid = (pred or {}).get("expected_gross_5d_pct")
    if pred_mid is None:
        pred_mid = (pred or {}).get("expected_return_5d_mid_pct")
    if pred_mid is not None:
        try:
            expected_pct = min(-1.0, float(pred_mid))
        except Exception:
            pass
    target = round(entry * (1 + expected_pct / 100.0), 2)
    rr = round(abs(entry - target) / abs(stop - entry), 2)
    return {
        "suggested_entry_pkr":  entry,
        "suggested_stop_pkr":   stop,
        "suggested_target_pkr": target,
        "risk_reward":          rr,
    }


# --- public API --------------------------------------------------------------


def rank_shorts(min_conviction: str = "LOW",
                 max_results: int = 25) -> dict:
    """Rank PSX universe by composite short_score.

    Parameters
    ----------
    min_conviction : str
        ``LOW`` (default) returns everything; pass ``MEDIUM`` or
        ``HIGH`` to filter to higher-conviction shorts only.
    max_results : int
        Cap on the returned list (already sorted by score desc).
    """
    from config.universe import symbols
    from config.short_eligibility import short_eligibility
    from ui import tools

    syms = symbols()

    # Pull each input ONCE for the whole universe; per-symbol
    # scoring then just looks up its slice. Keeps the function
    # cheap enough to call from a Streamlit reload.
    verdicts = _safe(
        lambda: __import__("brain.verdict_synthesizer",
                              fromlist=["synthesize_universe"])
                .synthesize_universe()) or {}
    verdict_by_sym = {v.get("symbol"): v
                       for v in (verdicts.get("rows") or [])}

    preds = _safe(lambda: tools.get_todays_predictions(
                          max_items=len(syms))) or {}
    pred_by_sym = {p.get("symbol"): p
                    for p in (preds.get("predictions") or [])}

    regime = _regime_adjustment()
    rows: list[dict] = []
    for sym in syms:
        verdict = verdict_by_sym.get(sym)
        pred = pred_by_sym.get(sym)
        sector = ((verdict or {}).get("sector")
                  or (pred or {}).get("sector") or "")

        s_synth, n_synth = _score_synth(verdict)
        s_pred,  n_pred  = _score_prediction(pred)
        s_news,  n_news  = _score_news(sym)
        tech = _safe(lambda: tools.get_technical_snapshot(sym), {})
        s_tech,  n_tech  = _score_technical(tech)
        s_macro, n_macro = _score_macro_headwind(sym, sector)
        s_intra, n_intra = _score_intraday_pressure(sym)

        total = round(s_synth + s_pred + s_news + s_tech +
                       s_macro + s_intra, 1)
        if total < 10:
            # Below 10 there really is no bearish lean worth showing.
            # We surface 10-44 as "watch" tier and 45+ as actionable;
            # this keeps the tab informative even on quiet sessions
            # where nothing is a strong short.
            continue

        drivers = [n for n in (n_synth, n_pred, n_news, n_tech,
                                  n_macro, n_intra) if n]
        # Get current price for level suggestions.
        price_block = _safe(lambda: tools.get_price(sym), {})
        price_now = (price_block or {}).get("close_pkr")
        levels = _suggest_levels(price_now, pred)

        conv = _bucket_conviction(total)
        if not regime.get("shorts_aligned"):
            conv = _downgrade(conv)

        # Squeeze-risk names get capped at MEDIUM.
        elig = short_eligibility(sym)
        if elig.get("squeeze_risk") and conv == "HIGH":
            conv = "MEDIUM"

        rows.append({
            "symbol":   sym,
            "sector":   sector,
            "current_price_pkr": price_now,
            "short_score":       total,
            "conviction":        conv,
            "verdict_action":    (verdict or {}).get("action"),
            "predicted_return_5d_pct": (
                (pred or {}).get("expected_gross_5d_pct")
                or (pred or {}).get("expected_return_5d_mid_pct")),
            "drivers":           drivers,
            "subscores": {
                "synth":     round(s_synth, 1),
                "prediction": round(s_pred, 1),
                "news":      round(s_news, 1),
                "technical": round(s_tech, 1),
                "macro":     round(s_macro, 1),
                "intraday":  round(s_intra, 1),
            },
            "eligibility": elig,
            **levels,
        })

    rows = sorted(rows, key=lambda r: -r["short_score"])
    rows = _apply_concentration_caps(rows)

    # Filter by min_conviction.
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    cutoff = rank.get(min_conviction.upper(), 0)
    rows = [r for r in rows
             if rank.get((r.get("conviction") or "LOW").upper(), 0)
                 >= cutoff]
    rows = rows[: int(max_results)]

    return {
        "as_of":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regime":  regime,
        "n_total": len(rows),
        "candidates": rows,
        "disclaimer": (
            "Pakistan retail shorting is restricted to PSX Single "
            "Stock Futures and NCCPL Securities Lending & Borrowing. "
            "Eligibility, borrow availability and margin "
            "requirements vary monthly — verify with your broker "
            "BEFORE acting on any short call from the bot. The "
            "short_score is a research signal, not a trade order."
        ),
    }
