"""Agent A: Macro Reader.

Distills the macro section of the briefing into a compact structured
summary:

    {
      "risk_regime":  "AGGRESSIVE"|"NORMAL"|"CAUTIOUS"|"DEFENSIVE"|"CASH",
      "regime_confidence": "LOW"|"MEDIUM"|"HIGH",
      "dominant_drivers":  [list of (tag, magnitude) tuples, top 5],
      "sector_tilts":      {sector: tilt_int},  # +2..-2 from macro_impact
      "bullets":           [5-7 plain-English bullets],
      "narrative":         1-paragraph human-readable summary,
      "tape_state":        {
        "kse100_5d_pct":    float,
        "kse100_21d_pct":   float,
        "universe_breadth": float,
        "vol_regime":       "QUIET"|"NORMAL"|"ELEVATED"|"CRISIS",
      },
      "key_event_window":  {"days_to": int, "name": str} or null,
    }

This is the FIRST output a user sees in the Today tab — a 30-second
read on "what is the macro tape saying right now?"

Fallback-first: every field is populated rule-based from the
deterministic macro_impact engine + regime classifier. Optional Claude
refinement adds the bullets and narrative quality.
"""
from __future__ import annotations

import os
from typing import Any


# ---------------------------------------------------------------------------
# Rule-based core (always runs)
# ---------------------------------------------------------------------------
def _classify_risk_regime(
    universe_5d: float | None,
    universe_21d: float | None,
    breadth: float | None,
    crisis_drivers: int,
) -> tuple[str, str]:
    """Map tape state -> risk_regime + confidence.

    Returns (regime, confidence)."""
    u5  = universe_5d  if universe_5d  is not None else 0.0
    u21 = universe_21d if universe_21d is not None else 0.0
    br  = breadth      if breadth      is not None else 0.5

    # Hard CASH/DEFENSIVE conditions
    if crisis_drivers >= 3 or u5 <= -0.05 or u21 <= -0.10:
        return "DEFENSIVE", "HIGH"
    if crisis_drivers >= 1 or u5 <= -0.02 or br < 0.30:
        return "CAUTIOUS", "MEDIUM"
    if u21 >= 0.08 and br >= 0.60 and u5 >= 0.01:
        return "AGGRESSIVE", "HIGH"
    if u21 >= 0.03 and br >= 0.50:
        return "NORMAL", "MEDIUM"
    return "NORMAL", "LOW"


def _tape_state(briefing: dict) -> dict:
    """Extract tape state from briefing.regime.indicators (canonical)
    or briefing.regime directly (replay shape)."""
    reg = briefing.get("regime") or {}
    ind = reg.get("indicators") or reg
    breadth = ind.get("breadth_pct_up_today") or ind.get("breadth_pct_up")
    if breadth is not None and breadth > 1.0:
        # canonical builder reports in 0-100; agent expects 0-1
        breadth = breadth / 100.0
    return {
        "kse100_5d_pct":     _to_pct(ind.get("universe_ret_5d")),
        "kse100_21d_pct":    _to_pct(ind.get("universe_ret_21d")),
        "universe_breadth":  breadth,
        "vol_regime":        _classify_vol(ind.get("universe_ret_5d")),
    }


def _classify_vol(univ_5d: float | None) -> str:
    if univ_5d is None:
        return "NORMAL"
    a = abs(univ_5d)
    if a >= 0.05:
        return "CRISIS"
    if a >= 0.03:
        return "ELEVATED"
    if a >= 0.015:
        return "NORMAL"
    return "QUIET"


def _to_pct(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x) * 100, 2)


def _dominant_drivers(briefing: dict, top_k: int = 5) -> list[dict]:
    mi = briefing.get("macro_impact") or {}
    drivers = mi.get("drivers") or []
    norm: list[dict] = []
    for d in drivers:
        if isinstance(d, dict):
            tag = d.get("tag")
            mag = d.get("magnitude") or "MEDIUM"
        elif isinstance(d, (list, tuple)) and len(d) >= 2:
            tag, mag = str(d[0]), str(d[1])
        else:
            continue
        if not tag:
            continue
        norm.append({"tag": tag, "magnitude": mag})
    # MAG ordering (canonical magnitudes seen in production):
    #   STRONG > MODERATE/MEDIUM > MILD
    order = {"STRONG": 0, "MODERATE": 1, "MEDIUM": 1, "MILD": 2}
    norm.sort(key=lambda d: order.get(d["magnitude"].upper(), 9))
    return norm[:top_k]


def _crisis_driver_count(drivers: list[dict]) -> int:
    """Count of distinct STRONG bear / risk-off drivers."""
    BEAR_TAGS = {
        "rate_up", "pkr_weak", "fx_blowout", "oil_demand_destruction",
        "btc_risk_off", "circular_debt_worsening",
        "imf_program_off_track", "sbp_rate_hike_shock",
        "geopolitical_oil_premium",   # neutral but raises vol
    }
    return sum(1 for d in drivers
               if d["tag"] in BEAR_TAGS and d["magnitude"] == "STRONG")


def _sector_tilts(briefing: dict) -> dict[str, int]:
    """Aggregate macro_impact's per-sector net tilt. Returns sector
    -> integer tilt in [-3, +3]. Reads ``by_sector`` (canonical) with
    fallback to ``per_sector`` (legacy)."""
    mi = briefing.get("macro_impact") or {}
    per_sec = (mi.get("by_sector") or mi.get("per_sector")
               or mi.get("sectors") or {})
    out: dict[str, int] = {}
    for sec, body in per_sec.items():
        if isinstance(body, dict):
            tilt = (body.get("net_tilt") or body.get("tilt")
                    or body.get("score") or body.get("net"))
        elif isinstance(body, (int, float)):
            tilt = body
        else:
            continue
        if tilt is None:
            continue
        try:
            out[sec] = int(round(float(tilt)))
        except (ValueError, TypeError):
            continue
    return out


def _key_event_window(briefing: dict) -> dict | None:
    """Return the soonest upcoming hard event (IMF review, MPS, etc.)."""
    calendar = (briefing.get("playbook_facts") or {}).get("active_events") or []
    upcoming = briefing.get("upcoming_events") or briefing.get("events") or []
    if not upcoming:
        return None
    soonest: dict | None = None
    for ev in upcoming:
        if not isinstance(ev, dict):
            continue
        d_to = ev.get("days_to") or ev.get("days_until")
        if d_to is None:
            continue
        try:
            d_to = int(d_to)
        except (TypeError, ValueError):
            continue
        if d_to < 0:
            continue
        if soonest is None or d_to < soonest["days_to"]:
            soonest = {
                "days_to": d_to,
                "name": str(ev.get("name") or ev.get("key") or "event"),
            }
    return soonest


def _build_bullets_rule_based(briefing: dict, summary: dict) -> list[str]:
    bullets: list[str] = []

    # 1) Tape state
    tape = summary["tape_state"]
    parts = []
    if tape["kse100_5d_pct"] is not None:
        parts.append(f"KSE-100 {tape['kse100_5d_pct']:+.2f}% over 5d")
    if tape["kse100_21d_pct"] is not None:
        parts.append(f"{tape['kse100_21d_pct']:+.2f}% over 21d")
    if tape["universe_breadth"] is not None:
        parts.append(f"breadth {tape['universe_breadth']*100:.0f}% up")
    if parts:
        bullets.append("Tape: " + ", ".join(parts) +
                       f"  [{tape['vol_regime']} vol]")

    # 2) Top 3 drivers
    for d in summary["dominant_drivers"][:3]:
        bullets.append(f"Driver: **{d['tag']}** ({d['magnitude']})")

    # 3) Sector tilts (top 3 by absolute magnitude)
    tilts = summary["sector_tilts"]
    if tilts:
        sorted_t = sorted(tilts.items(), key=lambda x: -abs(x[1]))[:3]
        bullet = "Sector tilts: " + ", ".join(
            f"{s} {t:+d}" for s, t in sorted_t)
        bullets.append(bullet)

    # 4) Event window
    ev = summary.get("key_event_window")
    if ev:
        bullets.append(
            f"Event window: **{ev['name']}** in {ev['days_to']}d "
            "(de-risk if <= 2d).")

    # 5) Regime call
    bullets.append(
        f"Recommended stance: **{summary['risk_regime']}** "
        f"(confidence: {summary['regime_confidence']}).")

    return bullets


def _build_narrative_rule_based(summary: dict) -> str:
    tape = summary["tape_state"]
    regime = summary["risk_regime"]
    drivers = summary["dominant_drivers"]
    if regime == "DEFENSIVE":
        opener = "Defensive stance triggered."
    elif regime == "CAUTIOUS":
        opener = "Cautious stance — tape weakness or active risk-off drivers."
    elif regime == "AGGRESSIVE":
        opener = "Aggressive stance — bullish breadth + sustained trend."
    else:
        opener = f"Normal stance ({summary['regime_confidence']} confidence)."

    driver_summary = (
        " Top driver: " + drivers[0]["tag"] +
        f" ({drivers[0]['magnitude']})." if drivers else "")
    tape_summary = ""
    if tape["kse100_5d_pct"] is not None:
        tape_summary = (
            f" Tape: KSE-100 {tape['kse100_5d_pct']:+.1f}%/5d, "
            f"{tape['vol_regime'].lower()} vol regime.")
    ev = summary.get("key_event_window")
    ev_note = (f" Next hard event: {ev['name']} in {ev['days_to']}d."
               if ev else "")
    return opener + driver_summary + tape_summary + ev_note


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def read_macro(briefing: dict, *, use_llm: bool = False) -> dict:
    """Run Agent A. Returns the macro summary structure.

    Always produces a full structure from rules. If `use_llm=True` and
    ANTHROPIC_API_KEY is set, optionally refines the bullets/narrative.
    The rule-based skeleton is always trustworthy and used downstream.
    """
    drivers = _dominant_drivers(briefing, top_k=8)
    crisis_n = _crisis_driver_count(drivers)
    reg = briefing.get("regime") or {}
    risk_regime, conf = _classify_risk_regime(
        reg.get("universe_ret_5d"),
        reg.get("universe_ret_21d"),
        reg.get("breadth_pct_up"),
        crisis_n,
    )

    summary: dict[str, Any] = {
        "risk_regime":       risk_regime,
        "regime_confidence": conf,
        "dominant_drivers":  drivers[:5],
        "sector_tilts":      _sector_tilts(briefing),
        "tape_state":        _tape_state(briefing),
        "key_event_window":  _key_event_window(briefing),
        "crisis_driver_count": crisis_n,
        "fallback_used":     True,
    }
    summary["bullets"]   = _build_bullets_rule_based(briefing, summary)
    summary["narrative"] = _build_narrative_rule_based(summary)

    # LLM refinement (optional)
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            llm_out = _refine_with_llm(briefing, summary)
            if llm_out:
                summary["bullets"]   = llm_out.get("bullets")   or summary["bullets"]
                summary["narrative"] = llm_out.get("narrative") or summary["narrative"]
                summary["fallback_used"] = False
        except Exception as e:
            summary["llm_error"] = f"{type(e).__name__}: {e}"

    return summary


def _refine_with_llm(briefing: dict, rule_summary: dict) -> dict | None:
    """Optional LLM pass: take the rule-based skeleton and produce
    richer bullets + narrative. Strictly does NOT change the regime
    classification or driver tags — those are deterministic."""
    from ui.llm_clients import ClaudeClient, MASTER_STRATEGIST_MODEL
    import json as _json

    macro_block = {
        "macro_snapshot":   briefing.get("macro_snapshot"),
        "macro_impact":     briefing.get("macro_impact"),
        "overnight":        briefing.get("overnight"),
        "policy_rate":      briefing.get("policy_rate"),
        "regime":           briefing.get("regime"),
        "mufap_industry":   briefing.get("mufap_industry"),
        "msci_calendar":    briefing.get("msci_calendar"),
        "rule_based_summary": rule_summary,
    }
    payload = _json.dumps(macro_block, default=str, indent=2)[:30_000]

    sys_prompt = (
        "You are the Macro Reader for a PSX trading bot. The user has "
        "already classified the regime deterministically; your job is "
        "to write 5-7 high-signal bullets and one paragraph explaining "
        "the macro setup. Stick to what the data shows; do NOT change "
        "the regime label or drivers. Output JSON only: "
        "{\"bullets\": [\"...\", ...], \"narrative\": \"...\"}."
    )
    history = [{"role": "user",
                "content": "Macro data block:\n```json\n" + payload +
                            "\n```\n\nReturn the JSON object only."}]
    client = ClaudeClient(model=MASTER_STRATEGIST_MODEL)
    result = client.run_chat(history=history, system=sys_prompt,
                              max_tokens=1_500, thinking_budget=512,
                              max_tool_iterations=0)
    text = (result.get("final_text") or "").strip()
    if "{" not in text:
        return None
    try:
        start = text.index("{")
        end   = text.rindex("}") + 1
        return _json.loads(text[start:end])
    except (ValueError, _json.JSONDecodeError):
        return None


if __name__ == "__main__":
    import json
    from pathlib import Path
    p = sorted(Path("data/_strategist").glob("_briefing_*.json"))[-1]
    briefing = json.loads(p.read_text(encoding="utf-8"))
    out = read_macro(briefing, use_llm=False)
    print(json.dumps(out, indent=2, default=str))
