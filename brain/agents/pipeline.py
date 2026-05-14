"""Multi-agent strategist pipeline orchestrator.

This is the single public entry point for the v2 strategist. It:

  1. Builds the briefing (delegates to brain.master_strategist.build_briefing).
  2. Runs Agent A (macro_reader) on the macro slice.
  3. Runs Agent B (stock_scorer) on the per-stock slice (using A's
     sector tilts).
  4. Runs Agent C (master_strategist_v2) on A+B + the original briefing.
  5. Applies the existing playbook overlay step (cash floors, size haircuts).
  6. Persists the decision in TWO schemas:
       - ``data/_strategist/YYYY-MM-DD.json``       (v1 schema, UI back-compat)
       - ``data/_strategist/YYYY-MM-DD_v2.json``    (v2 with per-tab guidance)
       - ``data/_strategist/latest.json``           (v1 — existing UI consumers)
       - ``data/_strategist/latest_v2.json``        (v2 — new UI consumers)

  All steps are wrapped so a failure in any one of them produces a
  fallback-style output and still writes the cache. The UI can ALWAYS
  read ``latest_v2.json`` and find a fully-populated structure.
"""
from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "_strategist"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Pipeline (public entry point)
# ---------------------------------------------------------------------------
def run_pipeline(
    *,
    use_llm: bool = True,
    account_size_pkr: float | None = None,
    write_cache: bool = True,
    deep: bool = False,
) -> dict[str, Any]:
    """Build briefing, run Agents A→B→C, apply playbook overlay, persist.

    Args:
        use_llm: When True and ANTHROPIC_API_KEY is set, agents
            optionally call Claude to refine bullets/narrative. When
            False or no API key, every agent runs purely on rules.
        account_size_pkr: Total account NAV. Used by the position-plan
            calculator to compute absolute PKR sizes.
        write_cache: When True, persist both v1 and v2 caches.
        deep: Currently a no-op; reserved for the deep-dive run that
            escalates to Opus.

    Returns:
        v2 decision dict. The v1 representation is also persisted to
        the cache so the existing UI keeps working unchanged.
    """
    from brain.master_strategist import (
        build_briefing as _build_briefing,
        _fallback_decision as _legacy_fallback,
        _finalise_with_overlays as _apply_overlays,
    )
    try:
        from ui.llm_clients import MASTER_STRATEGIST_MODEL
    except Exception:
        MASTER_STRATEGIST_MODEL = "claude-sonnet-4-5"
    from brain.agents.macro_reader import read_macro
    from brain.agents.stock_scorer import score_universe
    from brain.agents.master_strategist_v2 import decide_v2

    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_llm = use_llm and have_key

    # 1) Build briefing
    try:
        briefing = _build_briefing()
    except Exception as e:
        return _emergency_decision(f"build_briefing failed: {e}")

    errors: list[str] = []
    macro: dict[str, Any] = {}
    scored: list[dict] = []
    decision_v2_dict: dict[str, Any] = {}

    # 2) Agent A
    try:
        macro = read_macro(briefing, use_llm=use_llm)
    except Exception as e:
        errors.append(f"macro_reader: {e}")
        traceback.print_exc()

    # 3) Agent B
    try:
        scored = score_universe(briefing, macro_summary=macro, use_llm=use_llm)
    except Exception as e:
        errors.append(f"stock_scorer: {e}")
        traceback.print_exc()

    # 4) Agent C
    try:
        decision = decide_v2(briefing, macro=macro or None,
                              scored=scored or None,
                              account_size_pkr=account_size_pkr,
                              use_llm=use_llm)
        decision_v2_dict = decision.as_dict()
    except Exception as e:
        errors.append(f"master_strategist_v2: {e}")
        traceback.print_exc()

    if errors:
        decision_v2_dict["pipeline_errors"] = errors

    # 5) Build legacy v1 decision (for UI back-compat) + apply overlays
    legacy_v1 = _build_v1_compatible_decision(
        decision_v2_dict, briefing, MASTER_STRATEGIST_MODEL)
    try:
        legacy_v1 = _apply_overlays(legacy_v1, briefing, write_cache=False)
    except Exception as e:
        errors.append(f"overlay: {e}")

    # If overlays mutated the actions, mirror them back into v2.long_ideas
    try:
        _sync_overlay_changes(legacy_v1, decision_v2_dict)
    except Exception as e:
        errors.append(f"overlay-sync: {e}")

    decision_v2_dict.setdefault("pipeline_errors", errors)
    decision_v2_dict["briefing_summary"] = _briefing_summary(briefing)

    # 6) Persist
    if write_cache:
        _persist_v1(legacy_v1)
        _persist_v2(decision_v2_dict)
        _write_health_badge(decision_v2_dict, errors)

    return decision_v2_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_v1_compatible_decision(v2: dict, briefing: dict,
                                    model_name: str) -> dict:
    """Map v2 decision -> v1 schema so the existing UI keeps working."""
    today = v2.get("today_brief", {})
    macro = v2.get("macro_summary", {})
    risks = v2.get("risks_today", {}).get("risks", [])
    return {
        "as_of":         v2.get("as_of", datetime.now().strftime("%Y-%m-%d")),
        "model":         v2.get("model", model_name),
        "thinking_budget": 0,
        "headline":      v2.get("headline", ""),
        "risk_stance":   v2.get("regime", "NORMAL"),
        "conviction":    v2.get("regime_confidence", "LOW"),
        "narrative":     v2.get("narrative", ""),
        "agrees_with_phase1": True,
        "phase1_disagreement_note": "",
        "actions":       v2.get("actions", []),
        "key_drivers":   [d.get("tag") for d in (macro.get("dominant_drivers") or [])],
        "key_risks":     [r.get("note") for r in risks],
        "macro_lens":    macro.get("narrative", ""),
        "behavioural_lens": "",
        "fallback_used": v2.get("fallback_used", True),
        "raw_llm_text":  "",
        "thinking_trace": "",
        "briefing_summary": _briefing_summary(briefing),
    }


def _sync_overlay_changes(v1: dict, v2: dict) -> None:
    """If the playbook overlay haircut a position or raised cash, mirror
    the changes back into v2.long_ideas + v2.portfolio_review."""
    if not v2.get("long_ideas"):
        return
    actions_by_sym = {a.get("symbol"): a for a in v1.get("actions", [])
                       if a.get("symbol")}
    for idea in v2["long_ideas"].get("ideas", []):
        sym = idea.get("symbol")
        if not sym:
            continue
        a = actions_by_sym.get(sym)
        if not a:
            continue
        # Reflect any size change from overlay
        plan = idea.get("position_plan") or {}
        if a.get("target_weight_pct") is not None and plan:
            new_size = float(a["target_weight_pct"])
            if abs(plan.get("position_size_pct", 0) - new_size) > 0.01:
                plan["position_size_pct"] = new_size
                plan.setdefault("overlay_applied", []).append(
                    f"size adjusted to {new_size:.2f}% by playbook overlay")


def _briefing_summary(briefing: dict) -> dict:
    """Strip the giant briefing down to a 1-line summary the UI can
    show without ballooning the cache file."""
    out = {}
    for k in ("as_of", "regime", "predictions_count",
              "playbook_analogues_count", "n_universe"):
        if k in briefing:
            out[k] = briefing[k]
    if not out.get("regime") and briefing.get("regime"):
        out["regime"] = briefing["regime"].get("regime")
    preds = (briefing.get("predictions") or {}).get("predictions")
    if isinstance(preds, list):
        out["predictions_count"] = len(preds)
    pa = briefing.get("playbook_analogues")
    if isinstance(pa, list):
        out["playbook_analogues_count"] = len(pa)
    return out


def _persist_v1(decision: dict) -> None:
    try:
        date = decision.get("as_of") or datetime.now().strftime("%Y-%m-%d")
        (CACHE_DIR / f"{date}.json").write_text(
            json.dumps(decision, default=str, indent=2), encoding="utf-8")
        (CACHE_DIR / "latest.json").write_text(
            json.dumps(decision, default=str, indent=2), encoding="utf-8")
    except Exception:
        pass


def _persist_v2(decision: dict) -> None:
    try:
        date = decision.get("as_of") or datetime.now().strftime("%Y-%m-%d")
        (CACHE_DIR / f"{date}_v2.json").write_text(
            json.dumps(decision, default=str, indent=2), encoding="utf-8")
        (CACHE_DIR / "latest_v2.json").write_text(
            json.dumps(decision, default=str, indent=2), encoding="utf-8")
    except Exception:
        pass


def _write_health_badge(v2: dict, errors: list[str]) -> None:
    try:
        health_dir = PROJECT_ROOT / "data" / "_health"
        health_dir.mkdir(parents=True, exist_ok=True)
        badge = {
            "ts":          datetime.now().isoformat(),
            "pipeline":    "v2",
            "model":       v2.get("model", "unknown"),
            "fallback":    v2.get("fallback_used", True),
            "n_longs":     v2.get("long_ideas", {}).get("count", 0),
            "n_shorts":    v2.get("short_ideas", {}).get("count", 0),
            "regime":      v2.get("regime"),
            "errors":      errors,
            "ok":          (len(errors) == 0),
        }
        (health_dir / "strategist_v2.json").write_text(
            json.dumps(badge, default=str, indent=2), encoding="utf-8")
    except Exception:
        pass


def _emergency_decision(msg: str) -> dict:
    return {
        "as_of":   datetime.now().strftime("%Y-%m-%d"),
        "headline": "PIPELINE ERROR — using fallback",
        "regime":   "CAUTIOUS",
        "regime_confidence": "LOW",
        "today_brief":        {"headline": msg, "bullets": [], "narrative": msg},
        "long_ideas":         {"count": 0, "ideas": []},
        "short_ideas":        {"count": 0, "ideas": [], "note": msg},
        "portfolio_review":   {"n_holdings": 0, "holdings": []},
        "sector_view":        {"sectors": []},
        "watchlist":          {"count": 0, "names": []},
        "risks_today":        {"count": 1, "risks": [{"type":"pipeline","note":msg}]},
        "events_intelligence":{"count": 0, "events": []},
        "macro_summary":      {},
        "stock_scores":       [],
        "actions":            [],
        "fallback_used":      True,
        "model":              "emergency-fallback",
        "narrative":          msg,
        "pipeline_errors":    [msg],
        "version":            "v2",
    }


if __name__ == "__main__":
    out = run_pipeline(use_llm=False, account_size_pkr=1_000_000,
                       write_cache=True)
    print("Pipeline complete.")
    print(f"  headline:     {out.get('headline')}")
    print(f"  regime:       {out.get('regime')}")
    print(f"  longs:        {out['long_ideas']['count']}")
    print(f"  shorts:       {out['short_ideas']['count']}")
    print(f"  sectors:      {len(out['sector_view']['sectors'])}")
    print(f"  watchlist:    {out['watchlist']['count']}")
    print(f"  risks:        {out['risks_today']['count']}")
    print(f"  pipeline_errors: {out.get('pipeline_errors')}")
    print()
    print(f"Wrote: data/_strategist/latest_v2.json")
