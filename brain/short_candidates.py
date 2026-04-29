"""Composite short-candidate scorer.

Inverts the bot's existing bullish pipeline to surface stocks the
strategist thinks will go DOWN over the next ~5 sessions, ranked by
a transparent 0-100 ``short_score`` built from six bearish signal
buckets plus pre-event guards and a regime adjustment.

Dataset coverage
----------------
The scorer is wired into 11 of the bot's 12 live data sources, with
direct weighting where the signal is high-resolution and indirect
access (via the synthesizer or predictions log) where the signal is
already aggregated upstream:

  Direct + weighted in a bucket
    - OHLCV history (technical bucket)
    - Predictions log (prediction bucket)
    - Scored news per-symbol (news bucket)
    - Macro impact engine (macro bucket)
    - Intraday circuit breakers (intraday bucket)
    - Intraday MarketWatch (technical bucket: relative weakness)
    - Industry KPIs (macro bucket: sector-specific weakness)
    - Earnings calendar (pre-event guard)
    - MPC calendar / SBP meetings (pre-event guard)
    - Prediction critic notes (small score affirmation)

  Direct via the verdict synthesizer (synth bucket = 30 pts)
    - Fundamentals (Value + Quality lenses)
    - Director's reports (Management lens)
    - FIPI flows (Flow lens)

  Reached only through the LLM strategist (predictions bucket = 25 pts)
    - Material information notices
    - Overnight globals (S&P / VIX / DXY etc.)

  Not directly considered (low PSX-context signal)
    - Sector volume heatmap (already implicit in technical + macro)
    - Trade journal / user portfolio (those drive the long side; we
      do not bias shorts based on what the user already owns)

Score buckets (max 100)
-----------------------
  Verdict synthesizer (bearish lens score)             30
  5-day prediction expected return (if BEARISH)        25
  News sentiment over the trailing 7 days              15
  Technical breakdown + intraday relative weakness     15
  Macro headwind + industry KPI weakness               10
  Intraday lower-circuit hit in last 5 sessions         5

Pre-event guards (downgrade only — never add score)
---------------------------------------------------
  - Earnings within 5 days → cap conviction at MEDIUM. Binary
    earnings risk routinely overrides chart / news theses on PSX.
  - SBP MPC within 7 days for a rate-sensitive sector → cap at
    MEDIUM. Rate-sensitive shorts ahead of an MPC are pure-luck
    trades, not research.
  - Prediction critic flagged the prediction → +3 score affirmation
    (not a downgrade — the critic agrees the call is unsafe at
    HIGH conviction, which raises confidence in the bearish lean).

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


def _score_technical(tech: dict | None,
                       intraday_rs: float | None = None) -> tuple[float, str]:
    """0-15 from the technical posture + live intraday relative weakness.

    Awards points for three distinct breakdown patterns:
      * Overbought (RSI > 65) AND price below the 20-SMA (a textbook
        rolling-over setup).
      * Or strong negative 21-day return (< -7%).
      * Or persistent break below the 20-SMA (>3% below).

    On top of the daily-bar pattern, up to 3 bonus points are added if
    the intraday MarketWatch snapshot shows the stock underperforming
    the rest of the universe today (relative weakness vs market). This
    catches the "stock is bleeding while everything else is green"
    setup that is invisible on a daily chart.
    """
    if not tech or "error" in tech:
        # Even with no daily snapshot, a bad intraday print is signal.
        if intraday_rs is not None and intraday_rs <= -0.01:
            pts = _clamp((-intraday_rs - 0.01) / 0.04 * 3.0, 0.0, 3.0)
            return pts, (f"Intraday underperforming market by "
                         f"{intraday_rs * 100:+.1f}% — relative weakness")
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

    if intraday_rs is not None and intraday_rs <= -0.01:
        bonus = _clamp((-intraday_rs - 0.01) / 0.04 * 3.0, 0.0, 3.0)
        pts += bonus
        notes.append(f"Intraday relative weakness "
                      f"{intraday_rs * 100:+.1f}% vs market avg")

    pts = min(pts, 15.0)
    return pts, "; ".join(notes)


def _build_intraday_rs_map() -> dict[str, float]:
    """Map symbol -> relative-change vs universe median for today.

    Reads the latest snapshot from ``data/intraday/marketwatch.parquet``
    (written by the 11:30 / 13:30 PKT intraday workflow) and computes
    each ticker's intraday change minus the universe-median intraday
    change. Negative values mean the ticker is underperforming the
    market today; positive values mean it is leading.

    Returns an empty dict when no intraday snapshot is available, so
    the caller treats it as "neutral" rather than failing.
    """
    p = PROJECT_ROOT / "data" / "intraday" / "marketwatch.parquet"
    if not p.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_parquet(p)
        if df.empty:
            return {}
        df["snapshot_at"] = pd.to_datetime(df["snapshot_at"], utc=True,
                                              errors="coerce")
        latest_ts = df["snapshot_at"].max()
        snap = df[df["snapshot_at"] == latest_ts].copy()
        if snap.empty or "change_pct" not in snap.columns:
            return {}
        snap["change_pct"] = pd.to_numeric(snap["change_pct"],
                                              errors="coerce")
        median = float(snap["change_pct"].dropna().median() or 0.0)
        out: dict[str, float] = {}
        for _, r in snap.iterrows():
            sym = str(r.get("symbol") or "").upper().strip()
            chg = r.get("change_pct")
            if not sym or chg is None or pd.isna(chg):
                continue
            # Stored as percent (e.g. -1.4 = -1.4%). Convert to fraction.
            out[sym] = (float(chg) - median) / 100.0
        return out
    except Exception:
        return {}


def _score_macro_headwind(sym: str, sector: str,
                             macro_full: dict | None = None
                             ) -> tuple[float, str]:
    """0-10 from sector macro headwinds + industry KPI weakness.

    The macro engine already aggregates 12+ drivers (rates, oil,
    USD/PKR, gold, copper, cotton, T-bill, KIBOR, FX reserves,
    KSE-100 momentum, CPI, etc.) into per-sector verdicts. We award:
      * up to 7 pts for the count of explicit headwinds against
        the sector
      * up to 3 pts when the live industry KPI snapshot is also
        weak for this sector (cement dispatches falling, OMC sales
        weak, KIBOR spike for leveraged names, etc.)
    Capped at 10 pts to keep no single bucket dominant.
    """
    try:
        if macro_full is None:
            from brain.macro_impact import compute_macro_impact
            macro_full = compute_macro_impact(universe=[sym])
    except Exception:
        return 0.0, ""
    by_sec = (macro_full.get("by_sector") or {}).get(sector or "", {})
    headwinds = by_sec.get("headwinds") or []
    pts = 0.0
    notes: list[str] = []
    if headwinds:
        pts += _clamp(len(headwinds) * 3.0, 0.0, 7.0)
        notes.append(f"Macro headwind for {sector}: {headwinds[0]}")

    # Industry KPI tilt — surface negative momentum that pre-dates the
    # macro engine's headwind label (e.g. cement dispatches falling
    # MoM, OMC sales weak, KIBOR > 12% for leverage-heavy sectors).
    kpis = macro_full.get("kpis") or {}
    kibor = kpis.get("kibor_3m_pct")
    cpi   = kpis.get("cpi_yoy_pct")
    kse_5d = kpis.get("kse100_ret_5d_pct")
    sec_lc = (sector or "").lower()
    if (kibor is not None and float(kibor) >= 12.0
            and any(k in sec_lc for k in
                    ("auto", "cement", "real estate", "tech", "consumer"))):
        pts += 2.0
        notes.append(f"KIBOR 3M {kibor:.2f}% — leveraged-sector pressure")
    if (cpi is not None and float(cpi) >= 10.0
            and "consumer" in sec_lc):
        pts += 1.5
        notes.append(f"CPI YoY {cpi:.1f}% — consumer demand squeeze")
    if (kse_5d is not None and float(kse_5d) <= -2.0
            and any(k in sec_lc for k in
                    ("bank", "energy", "fertiliser"))):
        pts += 1.0
        notes.append(f"KSE-100 5d {kse_5d:+.1f}% — index leaders breaking")

    pts = min(pts, 10.0)
    return pts, "; ".join(notes)


def _check_pre_event_guards(sym: str, sector: str,
                              pred: dict | None) -> dict:
    """Return guards that should DOWNGRADE conviction.

    Two binary risks override almost any chart pattern on PSX and so
    are surfaced as conviction caps rather than score additions:

      * Earnings within 5 days. The result release moves the stock
        more than the prior week's chart pattern. Shorting into
        earnings is a coin-flip, not research.
      * SBP MPC within 7 days for a rate-sensitive sector
        (Banks, Cement, Auto, Power, Real Estate). A surprise rate
        cut would torch a short position even if the macro engine
        currently flags headwinds.

    Returns ``{}`` if no guard fires.
    """
    out: dict[str, str] = {}

    # Earnings calendar guard
    try:
        from brain.earnings_calendar import next_event
        ev = next_event((sym or "").upper())
        if ev and ev.get("days_until") is not None:
            d = int(ev.get("days_until"))
            if 0 <= d <= 5:
                out["earnings_guard"] = (
                    f"Earnings within {d} day(s) — conviction capped "
                    f"to MEDIUM. Binary results risk overrides chart "
                    f"and news theses on PSX.")
    except Exception:
        pass

    # MPC guard for rate-sensitive sectors
    try:
        mpc = (pred or {}).get("mpc_alert") or {}
        if not mpc:
            from brain.macro_impact import compute_macro_impact
            mi = compute_macro_impact(universe=[sym])
            mpc = mi.get("mpc_alert") or {}
        days_to_mpc = mpc.get("days_until")
        sensitive = {"Commercial Banks", "Cement", "Automobile Assembler",
                      "Automobile Parts & Accessories", "Power",
                      "Power Generation & Distribution",
                      "Real Estate Investment Trust", "Refinery"}
        if (days_to_mpc is not None and 0 <= int(days_to_mpc) <= 7
                and (sector or "") in sensitive):
            out["mpc_guard"] = (
                f"SBP MPC in {int(days_to_mpc)} day(s); {sector} is "
                f"rate-sensitive. Conviction capped to MEDIUM until "
                f"the rate decision is published.")
    except Exception:
        pass
    return out


def _score_critic_affirmation(pred: dict | None) -> tuple[float, str]:
    """0-3 if the prediction critic flagged the call as bearish-leaning.

    The critic runs deterministic post-LLM checks (KSE-100 trend,
    sector headwinds, valuation extremes, news sentiment vs LLM
    direction). If it added cautionary notes to a BEARISH prediction,
    that is independent confirmation worth a small score boost — not
    enough to dominate but enough to break ties between two
    otherwise-similar candidates.
    """
    if not pred:
        return 0.0, ""
    notes = pred.get("critic_notes") or []
    if not isinstance(notes, list) or not notes:
        return 0.0, ""
    direction = (pred.get("direction") or "").upper()
    if direction != "BEARISH":
        return 0.0, ""
    # Each critic note is a deterministic, named check; cap at 3 pts.
    pts = min(3.0, float(len(notes)))
    return pts, (f"Prediction critic flagged {len(notes)} caution(s) — "
                  "bearish thesis affirmed deterministically")


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

    # Pull macro impact for the whole universe ONCE so every per-symbol
    # call against `_score_macro_headwind` and the MPC guard reads the
    # same snapshot rather than recomputing 100x.
    try:
        from brain.macro_impact import compute_macro_impact
        macro_full = compute_macro_impact() or {}
    except Exception:
        macro_full = {}
    intraday_rs = _build_intraday_rs_map()

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
        s_tech,  n_tech  = _score_technical(tech, intraday_rs.get(sym))
        s_macro, n_macro = _score_macro_headwind(sym, sector, macro_full)
        s_intra, n_intra = _score_intraday_pressure(sym)
        s_critic, n_critic = _score_critic_affirmation(pred)

        total = round(s_synth + s_pred + s_news + s_tech +
                       s_macro + s_intra + s_critic, 1)
        if total < 10:
            # Below 10 there really is no bearish lean worth showing.
            # We surface 10-44 as "watch" tier and 45+ as actionable;
            # this keeps the tab informative even on quiet sessions
            # where nothing is a strong short.
            continue

        drivers = [n for n in (n_synth, n_pred, n_news, n_tech,
                                  n_macro, n_intra, n_critic) if n]
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

        # Pre-event guards (earnings, MPC) override conviction down.
        guards = _check_pre_event_guards(sym, sector, pred)
        if guards and conv == "HIGH":
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
                "critic":    round(s_critic, 1),
            },
            "guards":      guards,
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

    coverage = _build_coverage_map(macro_full, intraday_rs, preds,
                                       verdicts)

    return {
        "as_of":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regime":  regime,
        "n_total": len(rows),
        "candidates": rows,
        "dataset_coverage": coverage,
        "disclaimer": (
            "Pakistan retail shorting is restricted to PSX Single "
            "Stock Futures and NCCPL Securities Lending & Borrowing. "
            "Eligibility, borrow availability and margin "
            "requirements vary monthly — verify with your broker "
            "BEFORE acting on any short call from the bot. The "
            "short_score is a research signal, not a trade order."
        ),
    }


def _build_coverage_map(macro_full: dict,
                          intraday_rs: dict,
                          preds: dict,
                          verdicts: dict) -> dict:
    """Build a transparent map of which datasets are wired in.

    The map is rendered as the "Datasets considered" panel in the UI
    and the corresponding section in the daily PDF. We compute the
    actual availability flags here so the UI does not lie about a
    parquet that hasn't refreshed yet.
    """
    has_intraday_mw = bool(intraday_rs)
    cb_path = PROJECT_ROOT / "data" / "intraday" / "circuit_breakers.parquet"
    has_cb = cb_path.exists()
    has_predictions = bool((preds or {}).get("predictions"))
    has_synth = bool((verdicts or {}).get("rows"))
    has_macro = bool((macro_full or {}).get("by_sector"))
    has_industry_kpi = bool((macro_full or {}).get("kpis"))
    has_mpc = bool((macro_full or {}).get("mpc_alert"))
    try:
        from brain.earnings_calendar import universe_calendar
        has_earnings_cal = bool(universe_calendar(days_ahead=21)
                                 .get("by_symbol"))
    except Exception:
        has_earnings_cal = False

    def _row(name: str, weight: str, status: str, note: str) -> dict:
        return {"name": name, "weight": weight,
                "status": status, "note": note}

    direct = [
        _row("OHLCV daily history", "Technical bucket (up to 12 of 15 pts)",
             "ACTIVE", "RSI, 20-SMA distance, 21d momentum."),
        _row("Predictions log", "Prediction bucket (up to 25 pts)",
             "ACTIVE" if has_predictions else "MISSING",
             "BEARISH 5-day forecast scaled by magnitude + conviction."),
        _row("Scored news (per symbol, 7d)",
             "News bucket (up to 15 pts)", "ACTIVE",
             "Claude-graded sentiment, scaled by article count."),
        _row("Intraday MarketWatch",
             "Technical bucket bonus (up to 3 pts)",
             "ACTIVE" if has_intraday_mw else "STALE",
             "Live relative weakness vs universe median today."),
        _row("Intraday circuit breakers",
             "Intraday bucket (up to 5 pts)",
             "ACTIVE" if has_cb else "MISSING",
             "Lower-circuit hits in the last 5 sessions."),
        _row("Macro impact engine + macro series",
             "Macro bucket (up to 7 pts)",
             "ACTIVE" if has_macro else "MISSING",
             "Sector headwinds from rates, oil, USD/PKR, gold, etc."),
        _row("Industry KPIs (KIBOR, CPI, KSE-100 momentum)",
             "Macro bucket bonus (up to 3 pts)",
             "ACTIVE" if has_industry_kpi else "MISSING",
             "Leveraged-sector and consumer-demand pressure flags."),
        _row("Earnings calendar",
             "Pre-event guard (caps conviction)",
             "ACTIVE" if has_earnings_cal else "PARTIAL",
             "No HIGH conviction inside 5 days of earnings."),
        _row("SBP MPC calendar",
             "Pre-event guard (caps conviction)",
             "ACTIVE" if has_mpc else "MISSING",
             "Caps rate-sensitive sectors inside 7 days of MPC."),
        _row("Prediction critic notes",
             "Affirmation bonus (up to 3 pts)",
             "ACTIVE" if has_predictions else "MISSING",
             "Deterministic critic confirms bearish thesis."),
    ]
    via_synth = [
        _row("Fundamentals (P/E, P/B, ROE, dividends)",
             "Synth bucket — Value + Quality lenses",
             "ACTIVE" if has_synth else "MISSING",
             "Reaches the score via the verdict synthesizer (30 pts)."),
        _row("Director's reports / management tone",
             "Synth bucket — Management lens",
             "ACTIVE" if has_synth else "MISSING",
             "Latest management commentary tone in [-1, +1]."),
        _row("FIPI flows (foreign-vs-local)",
             "Synth bucket — Flow lens",
             "ACTIVE" if has_synth else "MISSING",
             "5-day average foreign net flow direction."),
    ]
    via_predictions = [
        _row("Material information notices", "Indirect — read by the LLM "
             "strategist when it composes the 5-day forecast",
             "ACTIVE" if has_predictions else "MISSING",
             "Notices flow into the prediction bucket through the LLM."),
        _row("Overnight global setup (S&P, VIX, DXY, Brent)",
             "Indirect — context for the LLM strategist",
             "ACTIVE" if has_predictions else "MISSING",
             "Bear-gap / risk-off cue absorbed by the prediction call."),
    ]
    not_directly = [
        _row("Sector volume heatmap",
             "Not directly weighted",
             "BY_DESIGN",
             "Already implicit in technical (volume confirms breaks) "
             "and macro (sector rotation) buckets."),
        _row("User portfolio / trade journal",
             "Not directly weighted",
             "BY_DESIGN",
             "Shorts must be evaluated on the data, not on what the "
             "user already owns."),
    ]
    return {
        "direct":          direct,
        "via_synthesizer": via_synth,
        "via_predictions": via_predictions,
        "not_directly":    not_directly,
        "summary": {
            "direct_count":          len(direct),
            "via_synth_count":       len(via_synth),
            "via_predictions_count": len(via_predictions),
            "not_directly_count":    len(not_directly),
            "total_datasets":        (len(direct) + len(via_synth)
                                       + len(via_predictions)
                                       + len(not_directly)),
        },
    }
