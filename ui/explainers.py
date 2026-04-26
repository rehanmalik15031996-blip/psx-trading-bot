"""Plain-English helpers for the UI.

Every quant label that surfaces on screen is translated here so a normal
investor can understand it without a glossary. Centralised so we stay
consistent across tabs.

Three families of helpers:

1. **Label translators** — ``value_label``, ``quality_label``,
   ``momentum_label``, ``regime_label``. Each returns
   ``(everyday_text, color, emoji)``.
2. **Narrative synthesizers** — ``market_mood`` and ``daily_narrative``
   read the dashboard ``brief`` dict and return a single human sentence
   you can paste into Markdown.
3. **Action picker** — ``top_action_today`` returns the single most
   important thing the user should consider doing.

The data source for all helpers is the dict returned by
``ui.dashboard_data.morning_brief()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


# ----------------------------------------------------------------------
# 1. Label translators
# ----------------------------------------------------------------------
VALUE_LABELS: dict[str, tuple[str, str]] = {
    "BUY_VALUE":  ("Looks cheap",       "green"),
    "FAIR":       ("Fairly priced",     "blue"),
    "SELL_VALUE": ("Looks expensive",   "red"),
    "NO_SIGNAL":  ("Not enough data",   "gray"),
}


QUALITY_LABELS: dict[str, tuple[str, str]] = {
    "HIGH":     ("Top tier business",   "green"),
    "MEDIUM":   ("Solid business",      "blue"),
    "LOW":      ("Watch out",           "orange"),
    "JUNK":     ("Avoid",               "red"),
    "UNKNOWN":  ("Unknown",             "gray"),
}


MOMENTUM_LABELS: dict[str, tuple[str, str]] = {
    "ACCELERATING":      ("Earnings growing fast",  "green"),
    "RECOVERING":        ("Bouncing back",          "green"),
    "STEADY":            ("Steady earnings",        "blue"),
    "DECELERATING":      ("Earnings slowing down",  "orange"),
    "EROSION":           ("Earnings shrinking",     "red"),
    "INSUFFICIENT_DATA": ("Not enough data",        "gray"),
}


REGIME_LABELS: dict[str, tuple[str, str]] = {
    "risk_on":      ("Market is in risk-on mode (favourable)", "green"),
    "risk_off":     ("Market is in risk-off mode (defensive)", "red"),
    "neutral":      ("Market is neutral",                       "blue"),
    "unknown":      ("Market regime unclear",                   "gray"),
}


CONVICTION_WORD: dict[str, str] = {
    "HIGH":   "high confidence",
    "MEDIUM": "moderate confidence",
    "LOW":    "low confidence",
}


def value_label(signal: str | None) -> tuple[str, str]:
    """Translate a fair-value signal into ``(text, color)``."""
    if not signal:
        return ("Not enough data", "gray")
    return VALUE_LABELS.get(signal, (signal, "gray"))


def quality_label(band: str | None) -> tuple[str, str]:
    """Translate a quality band (HIGH / MEDIUM / LOW / JUNK) into
    ``(text, color)``."""
    if not band:
        return ("Unknown", "gray")
    return QUALITY_LABELS.get(band, (band, "gray"))


def momentum_label(flag: str | None) -> tuple[str, str]:
    """Translate an earnings-momentum flag into ``(text, color)``."""
    if not flag:
        return ("—", "gray")
    return MOMENTUM_LABELS.get(flag, (flag, "gray"))


def regime_label(regime: str | None) -> tuple[str, str]:
    """Translate the market regime into ``(text, color)``."""
    if not regime:
        return ("Market regime unclear", "gray")
    return REGIME_LABELS.get(regime, (regime, "gray"))


def conviction_word(c: str | None) -> str:
    return CONVICTION_WORD.get((c or "").upper(), "low confidence")


def percent(v: float | None, decimals: int = 2,
            sign: bool = True) -> str:
    if v is None:
        return "—"
    fmt = f"{{:+.{decimals}f}}%" if sign else f"{{:.{decimals}f}}%"
    return fmt.format(v)


# ----------------------------------------------------------------------
# 2. Narrative synthesizers
# ----------------------------------------------------------------------
def time_of_day_greeting() -> str:
    """'morning' / 'afternoon' / 'evening' based on local clock."""
    h = datetime.now().hour
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    return "evening"


def market_mood(brief: dict[str, Any]) -> dict[str, Any]:
    """Aggregate the trader's brief into a single overall mood.

    Returns::

        {"label":   "Cautiously bullish",
         "color":   "green" | "orange" | "red" | "blue",
         "score":   0-100 (50 = neutral),
         "reasons": ["FIPI bought 2.3B yesterday", ...]}

    The score combines four sub-signals (each contributes ±15 from 50):

    * **Regime** — risk_on / risk_off / neutral
    * **Strategy filter** — exposure multiplier (0..1)
    * **Overnight gap prior** — predicted PSX open from global signals
    * **News sentiment** — macro-tilt from scored headlines
    """
    score = 50.0
    reasons: list[str] = []

    # ---- regime
    reg = (brief.get("regime") or {}).get("regime")
    expo = (brief.get("regime") or {}).get("exposure_multiplier", 1.0)
    if reg == "risk_on":
        score += 15
        reasons.append(
            f"Market regime is **risk-on** (PSX broadly trending up; "
            f"exposure ×{expo:.2f}).")
    elif reg == "risk_off":
        score -= 15
        reasons.append(
            f"Market regime is **risk-off** (PSX broadly weak; "
            f"trim exposure to ×{expo:.2f}).")
    else:
        reasons.append("Market regime is **neutral**.")

    # ---- overnight gap prior
    o = brief.get("overnight") or {}
    gap = (o.get("gap_prior") or {}).get("expected_gap_pct")
    bias = (o.get("gap_prior") or {}).get("bias", "")
    if gap is not None:
        score += max(-15, min(15, gap * 7.5))
        if abs(gap) < 0.1:
            reasons.append("Overnight global markets were quiet.")
        elif gap > 0:
            reasons.append(
                f"Overnight global markets are **supportive** "
                f"(predicted PSX open: {gap:+.2f}%, {bias}).")
        else:
            reasons.append(
                f"Overnight global markets are **weak** "
                f"(predicted PSX open: {gap:+.2f}%, {bias}).")

    # ---- news sentiment
    s = brief.get("sentiment") or {}
    macro = (s.get("macro") or {})
    senti = macro.get("score")
    n = macro.get("n", 0)
    if senti is not None and n > 0:
        score += max(-10, min(10, senti * 30))
        if senti > 0.15:
            reasons.append(
                f"News tone is **positive** (24h, {n} headlines).")
        elif senti < -0.15:
            reasons.append(
                f"News tone is **negative** (24h, {n} headlines).")
        else:
            reasons.append(
                f"News tone is **neutral** (24h, {n} headlines).")

    # ---- earnings blackout pressure
    cal = brief.get("earnings_calendar") or {}
    blackouts = cal.get("blackout_now") or []
    if blackouts:
        names = ", ".join(b["symbol"] for b in blackouts[:3])
        reasons.append(
            f"**Heads up:** {len(blackouts)} stock(s) report results "
            f"in the next 5 days ({names})."
        )

    score = max(0, min(100, round(score, 1)))

    if score >= 65:
        label, color = "Bullish today", "green"
    elif score >= 55:
        label, color = "Cautiously bullish", "green"
    elif score > 45:
        label, color = "Mixed / neutral", "blue"
    elif score > 35:
        label, color = "Cautiously bearish", "orange"
    else:
        label, color = "Bearish today", "red"

    return {"label": label, "color": color,
            "score": score, "reasons": reasons}


def daily_narrative(brief: dict[str, Any]) -> str:
    """A 2-3 sentence morning paragraph in everyday language."""
    parts: list[str] = []

    # Open with regime
    reg = (brief.get("regime") or {}).get("regime", "")
    reg_reason = (brief.get("regime") or {}).get("reason", "")
    if reg == "risk_on":
        parts.append(
            "PSX is in a **risk-on** environment — "
            "trends are up and most stocks are participating.")
    elif reg == "risk_off":
        parts.append(
            "PSX is in a **risk-off** environment — "
            "be defensive and reduce position sizes.")
    else:
        parts.append("PSX is in a **neutral** state.")

    # Overnight + sentiment
    o = brief.get("overnight") or {}
    gap = (o.get("gap_prior") or {}).get("expected_gap_pct")
    if gap is not None:
        if gap > 0.3:
            parts.append(
                f"Overnight global cues are **positive** "
                f"({gap:+.2f}% expected open).")
        elif gap < -0.3:
            parts.append(
                f"Overnight global cues are **negative** "
                f"({gap:+.2f}% expected open).")
        else:
            parts.append("Overnight global cues are calm.")

    # Top action
    act = top_action_today(brief)
    if act and act.get("symbol"):
        parts.append(
            f"Highest-conviction idea today: **{act['symbol']}** "
            f"({act.get('reason', '').lower()}).")

    # Blackout
    cal = brief.get("earnings_calendar") or {}
    bo = cal.get("blackout_now") or []
    if bo:
        names = ", ".join(b["symbol"] for b in bo[:3])
        parts.append(
            f"Avoid opening new positions on **{names}** — "
            f"earnings are due in the next ~5 days.")

    return " ".join(parts)


def top_action_today(brief: dict[str, Any]) -> dict[str, Any]:
    """Best single action right now, in plain English.

    Looks at today's stored predictions, filters BUY/ADD that clear the
    cost threshold, and picks the highest-conviction one. Returns
    ``{}`` if there is no strong action today (which is itself useful —
    "stay in cash" is a valid answer).
    """
    preds = brief.get("predictions") or {}
    rows = preds.get("predictions") or []
    actionable = [
        p for p in rows
        if p.get("suggested_action") in ("BUY", "ADD")
        and p.get("clears_cost_threshold")
        and (p.get("conviction") or "").upper() in ("HIGH", "MEDIUM")
    ]
    if not actionable:
        return {"symbol": None, "action": "Stay patient",
                "reason": "No high-conviction setups clear the cost "
                          "threshold today. Cash is a position."}
    # Sort by conviction (HIGH > MEDIUM) then expected return
    rank = {"HIGH": 2, "MEDIUM": 1}
    best = max(
        actionable,
        key=lambda p: (
            rank.get((p.get("conviction") or "").upper(), 0),
            float(p.get("expected_net_5d_pct") or 0),
        ),
    )
    return {
        "symbol": best.get("symbol"),
        "action": best.get("suggested_action", "BUY"),
        "conviction": best.get("conviction"),
        "entry":   best.get("entry_price_pkr"),
        "stop":    best.get("suggested_stop_pkr"),
        "target":  best.get("suggested_target_pkr"),
        "gross":   best.get("expected_gross_5d_pct"),
        "net":     best.get("expected_net_5d_pct"),
        "reason":  (best.get("rationale") or "")[:280],
    }


# ----------------------------------------------------------------------
# 3. Plain-English risk and alerts
# ----------------------------------------------------------------------
def alert_lines(brief: dict[str, Any]) -> list[dict[str, str]]:
    """Anything the user should be warned about right now.

    Each item is ``{"level": "warning"|"info", "text": "..."}``.
    """
    alerts: list[dict[str, str]] = []
    cal = brief.get("earnings_calendar") or {}
    for bo in cal.get("blackout_now") or []:
        alerts.append({
            "level": "warning",
            "text": (f"**{bo['symbol']}** likely reports on "
                     f"{bo['next_event_date_utc']} "
                     f"(in {bo['days_until']} days). Don't add — "
                     f"results-day moves are typically 5–10%."),
        })

    # Position-level alerts
    pf = brief.get("portfolio") or {}
    for pos in pf.get("positions") or []:
        ret_pct = pos.get("unrealized_return_pct")
        if ret_pct is None:
            continue
        sym = pos.get("symbol")
        if ret_pct >= 12:
            alerts.append({
                "level": "info",
                "text": f"**{sym}** is up {ret_pct:+.1f}% — "
                         f"consider booking partial profits.",
            })
        elif ret_pct <= -8:
            alerts.append({
                "level": "warning",
                "text": f"**{sym}** is down {ret_pct:+.1f}% — "
                         f"review your stop-loss.",
            })

    return alerts
