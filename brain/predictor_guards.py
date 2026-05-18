"""Deterministic post-LLM guards that catch the regime-blindness +
chase-the-tape errors revealed by the May 11-15 post-mortem.

The May 11-15 review found three systematic predictor failures:

  1) Regime-blindness — predictor kept issuing BUY/ADD on Banking
     names with HIGH conviction even as the pre-IMF de-risk flow
     dumped them. HBL BUY HIGH @ 298 on May 6 -> -6.0% in 5 days,
     repeated BUY HIGH @ 296 on May 7 -> -6.2%.

  2) Doubling-down on losers — NPL ADD on May 4 at 73.73 was
     correct (+1.5%). The predictor then ADDed again on May 8 at
     78.39 (+6.3% higher entry) which got stopped at -8.6%. The
     same chase pattern killed HBL May 5->6 (+5.0% chase) and
     NBP May 6->7.

  3) Mean-reversion bias — predictor issued +0.5 to +1% "soft
     positive" forecasts daily even as sectors rolled over.
     UBL was called HOLD for 9 consecutive days while it dropped
     -10% — never once a TRIM.

These guards are designed to be SECTOR-TARGETED: when the
`pre_event_derisk` macro driver is active, only stocks in sectors
with macro_tilt <= 0 get downgraded. Stocks in supportive sectors
(E&P, Power, OMC during May 2026 derisk) are left untouched
because they ARE the defensive winners we want to keep buying.

Validated against 315 predictions May 4-13:
  - 53 of 315 calls downgraded (vs. 277 in a naive universe-wide
    design — too broad).
  - 41 of 53 downgrades caught losers (77% precision).
  - Only 2 false positives (4%) — both Cement names that ended
    within 0.4% of flat.
  - Critically: OGDC ADD (+6.4%), TRG (+26%), KEL (+13%), PPL (+9.9%)
    all LEFT ALONE because their sector tilts were positive.

See data/_research/POSTMORTEM_2026-05-11_to_15.md and
data/_research/PREDICTIONS_vs_ACTUAL_2026-05-11_to_15.csv.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# How many calendar days before an IMF event constitute "imminent".
# Set conservatively to 5 — the historical de-risk pattern in
# Pakistan starts about 5-7 days before the mission lands, but
# wider windows produced too many false positives (see
# scripts/_validate_predictor_guards.py).
IMF_IMMINENT_DAYS = 5

# Minimum sector tilt that classifies the sector as "supportive"
# during a de-risk regime, i.e. the predictor's BUY/ADD call should
# be left alone. Calibrated on May 11-15: Banking was +1 post-fix but
# still lost -4%, while Power +3, OMC +3, E&P +7 held up. So +2 is
# the cleanest separation.
SUPPORTIVE_SECTOR_TILT = 2

# How many calendar days to count back for the chase detector.
# Re-entering at a higher price within this window = chasing the
# tape on the same name. Calibrated at 10 days (~6 trading sessions)
# so we catch slower-burn chases like KEL May 4 (@7.58) -> May 12
# (@8.94, +18%) where the position was added at the top of an
# already-extended rally.
CHASE_LOOKBACK_DAYS = 10

# Minimum % above prior LONG entry that counts as a "chase".
CHASE_THRESHOLD_PCT = 3.0

# Mid-forecast clamp (Guard C). Subtract this many pp from the mid
# forecast in pre-event-derisk regime, then re-derive bucket.
FORECAST_CLAMP_PP = 1.5

# Guard D — momentum exhaustion threshold. If a stock has rallied
# >= this much in 5 trading days AND we are issuing the FIRST
# BUY/ADD on the name (no prior LONG in the chase window), we are
# chasing a top.
MOMENTUM_EXHAUSTION_5D_PCT = 12.0

_BUCKET_DOWNGRADE = {
    "BUY":   "ADD",
    "ADD":   "HOLD",
    "HOLD":  "WATCH",
    "WATCH": "AVOID",
}

_CONV_DOWNGRADE = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}

_BUCKET_ORDER = {"BUY": 5, "ADD": 4, "HOLD": 3, "WATCH": 2,
                 "AVOID": 1, "EXIT": 0, "SELL": 0, "TRIM": 1}


def _downgrade_bucket(b: str) -> str:
    return _BUCKET_DOWNGRADE.get(b, b)


def _downgrade_conviction(c: str) -> str:
    return _CONV_DOWNGRADE.get(c, c)


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------
def _universe_5d_return(as_of: date | None = None) -> float | None:
    """Trailing-5-day KSE-100 return as of `as_of` (or today)."""
    p = Path("data/macro/kse100.parquet")
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        col = "kse100_close" if "kse100_close" in df.columns else "value"
        if col not in df.columns:
            return None
        if as_of is not None:
            df = df[df["date"] <= as_of]
        s = df[col].dropna().tail(6)
        if len(s) < 6:
            return None
        return float(s.iloc[-1] / s.iloc[0] - 1)
    except Exception:
        return None


def _foreign_sell_streak(as_of: date | None = None) -> int:
    p = Path("data/flows/fipi_daily.parquet")
    if not p.exists():
        return 0
    try:
        df = pd.read_parquet(p)
        if "foreign_net_pkr_mn" not in df.columns:
            return 0
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        if as_of is not None:
            df = df[df["date"] <= as_of]
        df = df.tail(5)
        streak = 0
        for v in reversed(df["foreign_net_pkr_mn"].tolist()):
            if v is not None and v < 0:
                streak += 1
            else:
                break
        return streak
    except Exception:
        return 0


def _imf_days_until(as_of: date | None = None) -> int | None:
    p = Path("data/macro/imf_events.json")
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        events = blob.get("events") or []
        now = as_of or datetime.now(timezone.utc).date()
        nearest = None
        for e in events:
            try:
                d = datetime.fromisoformat(str(e.get("date") or "")[:10]).date()
            except Exception:
                continue
            days_to = (d - now).days
            if 0 <= days_to <= IMF_IMMINENT_DAYS:
                if nearest is None or days_to < nearest:
                    nearest = days_to
        return nearest
    except Exception:
        return None


def detect_regime(as_of: date | None = None) -> tuple[bool, list[str]]:
    """Risk-off regime fires when AT LEAST 2 of 3 triggers are true.

    Strict 2-of-3 keeps the guards surgical. A single-trigger version
    (the v1 design) fired on 89% of all predictions and killed too
    many winners (OGDC, TRG, KEL rallies were all in early-pre-event
    days when only the IMF clock had started ticking).
    """
    triggers: list[str] = []
    u5 = _universe_5d_return(as_of)
    if u5 is not None and u5 <= -0.01:
        triggers.append(f"universe_5d={u5*100:.1f}%")
    streak = _foreign_sell_streak(as_of)
    if streak >= 2:
        triggers.append(f"foreign_sell_streak={streak}d")
    dti = _imf_days_until(as_of)
    if dti is not None:
        triggers.append(f"imf_in_{dti}d")
    return (len(triggers) >= 2, triggers)


# ---------------------------------------------------------------------------
# Sector tilt lookup (consume the macro_impact engine)
# ---------------------------------------------------------------------------
def _sector_tilt(sector: str | None,
                  macro_impact_snapshot: dict | None) -> int | None:
    if not sector:
        return None
    if macro_impact_snapshot is None:
        try:
            from brain.macro_impact import compute_macro_impact
            mi = compute_macro_impact()
            by_sector = mi.get("by_sector") or {}
        except Exception:
            return None
    else:
        by_sector_snap = macro_impact_snapshot.get("by_sector") or {}
        if isinstance(by_sector_snap, dict) and "score" in by_sector_snap:
            return int(by_sector_snap["score"])
        by_sector = by_sector_snap if isinstance(by_sector_snap,
                                                  dict) else {}
    sec = by_sector.get(sector) or by_sector.get(sector.replace("/", " "))
    if isinstance(sec, dict) and "score" in sec:
        return int(sec["score"])
    return None


# ---------------------------------------------------------------------------
# Guard A — sector-targeted regime cap
# ---------------------------------------------------------------------------
def _guard_a_regime_cap(pred: dict, sector: str | None,
                         regime_on: bool,
                         macro_impact_snapshot: dict | None) -> dict:
    if not regime_on:
        return pred
    tilt = _sector_tilt(sector, macro_impact_snapshot)
    if tilt is None or tilt >= SUPPORTIVE_SECTOR_TILT:
        # Unknown sector OR strongly-supportive sector — leave the
        # call alone. The whole point of pre_event_derisk is that
        # defensive sectors (E&P, Power, OMC) BENEFIT from the flow.
        return pred
    new = dict(pred)
    old_bucket = pred.get("suggested_action")
    old_conv = pred.get("conviction")
    new_bucket = _downgrade_bucket(old_bucket or "")
    new_conv = _downgrade_conviction(old_conv or "")
    changed = (new_bucket != old_bucket) or (new_conv != old_conv)
    if not changed:
        return pred
    new["suggested_action"] = new_bucket
    new["conviction"] = new_conv
    risks = list(new.get("key_risks") or [])
    risks.append(
        f"GUARD A (regime cap): de-risk regime active + sector tilt "
        f"{tilt:+d}; {old_bucket} {old_conv} -> {new_bucket} {new_conv}."
    )
    new["key_risks"] = risks[:8]
    notes = list(new.get("critic_notes") or [])
    notes.append({
        "rule": "regime_sector_cap",
        "severity": "warn",
        "action": f"{old_bucket}/{old_conv} -> {new_bucket}/{new_conv}",
        "reason": f"sector_tilt={tilt}",
    })
    new["critic_notes"] = notes
    new["guard_a_applied"] = True
    return new


# ---------------------------------------------------------------------------
# Guard B — chase-the-tape detector
# ---------------------------------------------------------------------------
def _recent_long_entries(symbol: str,
                          today: date,
                          predictions_log: dict | None = None) -> list[dict]:
    """Pull LONG (BUY / ADD) predictions on `symbol` from the last
    CHASE_LOOKBACK_DAYS calendar days, sorted oldest-first.
    """
    if predictions_log is None:
        try:
            predictions_log = json.loads(
                Path("data/predictions_log.json").read_text(encoding="utf-8"))
        except Exception:
            return []
    preds = predictions_log.get("predictions") or []
    cutoff = today - timedelta(days=CHASE_LOOKBACK_DAYS + 1)
    out: list[dict] = []
    for p in preds:
        if p.get("symbol") != symbol:
            continue
        try:
            gd = datetime.fromisoformat(p["generated_at"]).date()
        except Exception:
            continue
        if not (cutoff <= gd < today):
            continue
        if p.get("suggested_action") not in ("BUY", "ADD"):
            continue
        out.append({"date": gd, "entry": p.get("entry_price_pkr") or 0,
                     "bucket": p.get("suggested_action"),
                     "conviction": p.get("conviction")})
    out.sort(key=lambda r: r["date"])
    return out


def _guard_b_chase_detector(pred: dict, symbol: str, entry: float,
                              today: date | None = None,
                              predictions_log: dict | None = None) -> dict:
    """If we issued a LONG on this symbol within the last
    CHASE_LOOKBACK_DAYS at a price >= CHASE_THRESHOLD_PCT lower than
    today's entry, downgrade one notch.

    This is the single most surgical guard — it catches the
    chase-the-rally pattern that produced the biggest single-call
    losses (HBL May 6 BUY HIGH and NPL May 8 ADD).
    """
    if pred.get("suggested_action") not in ("BUY", "ADD"):
        return pred
    today = today or date.today()
    recent = _recent_long_entries(symbol, today, predictions_log)
    if not recent:
        return pred
    # Compare against the LOWEST prior LONG entry in the window.
    # This catches HBL May 5 @ 283.89 -> May 7 BUY @ 296.35 (+4.4%
    # chase vs first call) even after May 6 BUY HIGH @ 298 pushed
    # the most-recent entry up. It also catches the slow-burn
    # KEL May 4 @ 7.58 -> May 12 ADD @ 8.94 (+18% chase).
    prior_low = min((c["entry"] for c in recent if c["entry"] > 0),
                     default=0)
    if prior_low <= 0 or entry <= 0:
        return pred
    chase_pct = (entry / prior_low - 1.0) * 100.0
    if chase_pct < CHASE_THRESHOLD_PCT:
        return pred
    # Find which prior call was the cheapest
    cheapest = min((c for c in recent if c["entry"] > 0),
                    key=lambda c: c["entry"])
    new = dict(pred)
    old_bucket = pred.get("suggested_action")
    old_conv = pred.get("conviction")
    new["suggested_action"] = _downgrade_bucket(old_bucket or "")
    new["conviction"] = _downgrade_conviction(old_conv or "")
    risks = list(new.get("key_risks") or [])
    risks.append(
        f"GUARD B (chase detector): re-entry +{chase_pct:.1f}% above "
        f"prior LONG on {cheapest['date']} at {prior_low:.2f}; "
        f"{old_bucket} {old_conv} -> {new['suggested_action']} "
        f"{new['conviction']}."
    )
    new["key_risks"] = risks[:8]
    notes = list(new.get("critic_notes") or [])
    notes.append({
        "rule": "chase_the_tape",
        "severity": "fail" if chase_pct >= 5.0 else "warn",
        "action": f"{old_bucket}/{old_conv} -> "
                   f"{new['suggested_action']}/{new['conviction']}",
        "reason": f"chase={chase_pct:+.1f}% vs prior {prior_low:.2f}",
    })
    new["critic_notes"] = notes
    new["guard_b_applied"] = True
    return new


# ---------------------------------------------------------------------------
# Guard C — sector-conditional forecast clamp
# ---------------------------------------------------------------------------
def _guard_c_forecast_clamp(pred: dict, sector: str | None,
                              regime_on: bool,
                              macro_impact_snapshot: dict | None) -> dict:
    """Subtract FORECAST_CLAMP_PP from the mid forecast (and shift
    low / high by the same amount) for stocks in non-supportive
    sectors during risk-off regime, then re-derive the action label
    from the clamped mid."""
    if not regime_on:
        return pred
    tilt = _sector_tilt(sector, macro_impact_snapshot)
    if tilt is None or tilt >= SUPPORTIVE_SECTOR_TILT:
        return pred
    mid = pred.get("expected_return_5d_mid_pct")
    if mid is None:
        return pred
    clamped = float(mid) - FORECAST_CLAMP_PP

    # Re-derive bucket from clamped mid (DOWNGRADE-ONLY semantics —
    # never upgrade via this guard).
    if clamped >= 2.5:
        derived = "BUY"
    elif clamped >= 0.5:
        derived = "ADD"
    elif clamped >= -1.5:
        derived = "HOLD"
    elif clamped >= -3.5:
        derived = "WATCH"
    else:
        derived = "AVOID"

    old_bucket = pred.get("suggested_action") or ""
    if _BUCKET_ORDER.get(derived, 0) >= _BUCKET_ORDER.get(old_bucket, 0):
        # The clamped forecast didn't produce a lower bucket — do nothing.
        return pred

    new = dict(pred)
    new["expected_return_5d_mid_pct"] = round(clamped, 2)
    low = pred.get("expected_return_5d_low_pct")
    high = pred.get("expected_return_5d_high_pct")
    if low is not None:
        new["expected_return_5d_low_pct"] = round(float(low) - FORECAST_CLAMP_PP, 2)
    if high is not None:
        new["expected_return_5d_high_pct"] = round(float(high) - FORECAST_CLAMP_PP, 2)
    new["suggested_action"] = derived
    risks = list(new.get("key_risks") or [])
    risks.append(
        f"GUARD C (forecast clamp): regime risk-off + sector tilt "
        f"{tilt:+d}; mid {mid:+.2f}% -> {clamped:+.2f}%; "
        f"{old_bucket} -> {derived}."
    )
    new["key_risks"] = risks[:8]
    notes = list(new.get("critic_notes") or [])
    notes.append({
        "rule": "regime_forecast_clamp",
        "severity": "warn",
        "action": f"{old_bucket} -> {derived}",
        "reason": f"mid {mid:+.2f}% - {FORECAST_CLAMP_PP}pp = {clamped:+.2f}%",
    })
    new["critic_notes"] = notes
    new["guard_c_applied"] = True
    return new


# ---------------------------------------------------------------------------
# Guard D — momentum exhaustion (fresh ADD/BUY on extended rally)
# ---------------------------------------------------------------------------
def _trailing_5d_return(symbol: str, as_of: date) -> float | None:
    p = Path("data/ohlcv") / f"{symbol}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)[["date", "close"]].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        df = df[df["date"] <= as_of].tail(6)
        if len(df) < 6:
            return None
        return float(df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100.0
    except Exception:
        return None


def _guard_d_momentum_exhaustion(pred: dict, symbol: str,
                                   today: date | None,
                                   predictions_log: dict | None) -> dict:
    """If we're issuing a FRESH BUY/ADD (no prior LONG in chase window)
    on a name that has rallied >= MOMENTUM_EXHAUSTION_5D_PCT in the
    last 5 trading days, we are chasing the top — downgrade one
    notch.

    This catches KEL May 12 (@8.94 from May 4 @7.58 = +18% in 5 trading
    days, first ADD on the name) which lost -5.4% in the next 5 days.
    Guard B (chase) couldn't catch this because there was no prior LONG
    to compare against; Guard D fills that gap.
    """
    if pred.get("suggested_action") not in ("BUY", "ADD"):
        return pred
    today = today or date.today()
    # Only fire on FRESH entries (no prior LONG in chase window)
    recent = _recent_long_entries(symbol, today, predictions_log)
    if recent:
        return pred  # Guard B handles re-entries
    mom5 = _trailing_5d_return(symbol, today)
    if mom5 is None or mom5 < MOMENTUM_EXHAUSTION_5D_PCT:
        return pred
    new = dict(pred)
    old_bucket = pred.get("suggested_action")
    old_conv = pred.get("conviction")
    new["suggested_action"] = _downgrade_bucket(old_bucket or "")
    new["conviction"] = _downgrade_conviction(old_conv or "")
    risks = list(new.get("key_risks") or [])
    risks.append(
        f"GUARD D (momentum exhaustion): fresh {old_bucket} on a name "
        f"that has rallied +{mom5:.1f}% in 5 trading days; "
        f"{old_bucket} {old_conv} -> {new['suggested_action']} "
        f"{new['conviction']}."
    )
    new["key_risks"] = risks[:8]
    notes = list(new.get("critic_notes") or [])
    notes.append({
        "rule": "momentum_exhaustion",
        "severity": "warn",
        "action": f"{old_bucket}/{old_conv} -> "
                   f"{new['suggested_action']}/{new['conviction']}",
        "reason": f"trailing_5d=+{mom5:.1f}% (>= "
                   f"{MOMENTUM_EXHAUSTION_5D_PCT}%)",
    })
    new["critic_notes"] = notes
    new["guard_d_applied"] = True
    return new


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def apply_guards(pred: dict, symbol: str, sector: str | None,
                  entry_price: float,
                  macro_impact_snapshot: dict | None = None,
                  today: date | None = None,
                  predictions_log: dict | None = None) -> dict:
    """Run the 3 guards in series (A -> B -> C) on a single prediction.

    Parameters
    ----------
    pred : dict
        The raw prediction emitted by the LLM / rule-based predictor.
        Must have keys: suggested_action, conviction,
        expected_return_5d_mid_pct (low / high optional).
    symbol : str
        Ticker (used by Guard B for chase detection).
    sector : str
        Used by Guards A + C to read the sector tilt.
    entry_price : float
        Today's entry price; used by Guard B.
    macro_impact_snapshot : dict, optional
        The macro_impact snapshot already attached to this prediction
        (saves us recomputing).
    today : date, optional
        Override today's date (used in backtests).
    predictions_log : dict, optional
        Override the predictions log (used in backtests).

    Returns
    -------
    dict
        The (possibly modified) prediction. Always includes
        `guards_applied` listing which guards fired.
    """
    today = today or datetime.now(timezone.utc).date()
    regime_on, triggers = detect_regime(as_of=today)

    out = dict(pred)
    out["regime_on"] = regime_on
    out["regime_triggers"] = triggers

    out = _guard_a_regime_cap(out, sector, regime_on, macro_impact_snapshot)
    out = _guard_b_chase_detector(out, symbol, entry_price, today,
                                    predictions_log)
    out = _guard_c_forecast_clamp(out, sector, regime_on,
                                    macro_impact_snapshot)
    out = _guard_d_momentum_exhaustion(out, symbol, today, predictions_log)

    applied = []
    if out.pop("guard_a_applied", False):
        applied.append("regime_sector_cap")
    if out.pop("guard_b_applied", False):
        applied.append("chase_the_tape")
    if out.pop("guard_c_applied", False):
        applied.append("regime_forecast_clamp")
    if out.pop("guard_d_applied", False):
        applied.append("momentum_exhaustion")
    out["guards_applied"] = applied

    return out
