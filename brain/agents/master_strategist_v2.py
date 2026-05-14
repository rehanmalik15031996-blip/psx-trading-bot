"""Agent C: Master Strategist v2 — the intelligent top layer.

Consumes:
  - Agent A (macro summary): bullets, regime, sector tilts, event window
  - Agent B (per-stock scores): 35 ranked rows with action + components
  - briefing.playbook_analogues: historical case matches
  - briefing.portfolio: existing positions (so we can ADD/TRIM intelligently)
  - briefing.predictions: 5d forecasts for cross-reference

Produces a MasterDecisionV2 that populates every UI tab:
  - today_brief        -> Today / Decision tab
  - portfolio_review   -> Portfolio tab (per-holding action + stop)
  - long_ideas         -> Long Ideas / BUY tab
  - short_ideas        -> Short Ideas tab
  - sector_view        -> Sector Outlook tab
  - watchlist          -> Watchlist tab
  - risks_today        -> Risks tab
  - events_intelligence -> Events tab (upcoming + reaction plan)

Every BUY/ADD recommendation is annotated with a position plan
(entry / stop_loss / target / size) via brain.risk.stop_loss.

Fallback-first: the rule-based path produces a fully-populated decision
without the LLM. Optional Claude refinement only enriches the narrative
text — it cannot change actions or stops (those are deterministic).

This is the ONLY agent that consumes both A's output AND the original
briefing — so the master strategist still "sees everything" but with
the macro and per-stock context already digested. No truncation risk.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helper: per-tab builders (all deterministic, no LLM)
# ---------------------------------------------------------------------------
def _today_brief(macro: dict, scored: list[dict],
                  briefing: dict) -> dict:
    """Headline + narrative for the Today tab."""
    regime = macro.get("risk_regime", "NORMAL")
    longs  = [r for r in scored if r["score"] >= 0.30][:5]
    shorts = [r for r in scored if r["score"] <= -0.30][:3]

    if regime == "DEFENSIVE":
        headline = "DEFENSIVE — raise cash, tighten stops"
    elif regime == "CAUTIOUS":
        headline = "CAUTIOUS — selective adds only, no new shorts"
    elif regime == "AGGRESSIVE":
        headline = f"AGGRESSIVE — {len(longs)} HIGH-conviction longs"
    else:
        if len(longs) >= 3:
            headline = f"NORMAL — selective longs ({longs[0]['symbol']}, {longs[1]['symbol']} top)"
        elif len(longs) >= 1:
            headline = f"NORMAL — single conviction long ({longs[0]['symbol']})"
        else:
            headline = "NORMAL — no high-conviction names, stay defensive"

    bullets = macro.get("bullets", [])[:4]
    if longs:
        bullets.append(
            "Top long: **" + longs[0]["symbol"] + "** "
            f"(score {longs[0]['score']:+.2f}, "
            f"{longs[0]['action']}, {longs[0]['conviction']} conv) — "
            + longs[0]["why"][:120])
    if shorts:
        bullets.append(
            "Top short: **" + shorts[0]["symbol"] + "** "
            f"(score {shorts[0]['score']:+.2f}, {shorts[0]['action']})")

    return {
        "headline":  headline,
        "regime":    regime,
        "regime_confidence": macro.get("regime_confidence"),
        "bullets":   bullets,
        "narrative": macro.get("narrative", ""),
        "n_longs":   len(longs),
        "n_shorts":  len(shorts),
        "key_event_window": macro.get("key_event_window"),
    }


def _long_ideas(scored: list[dict], briefing: dict,
                  account_size_pkr: float | None = None) -> dict:
    """Top long candidates with full position plans."""
    from brain.risk.stop_loss import compute_position_plan

    longs = [r for r in scored if r["score"] >= 0.10][:10]
    ideas = []
    for r in longs:
        plan = compute_position_plan(
            symbol=r["symbol"], sector=r["sector"],
            entry_price=r.get("entry_price"),
            trade_style="swing",
            account_size_pkr=account_size_pkr,
        )
        entry = {
            "symbol":            r["symbol"],
            "sector":            r["sector"],
            "score":             r["score"],
            "action":            r["action"],
            "conviction":        r["conviction"],
            "expected_5d_pct":   r.get("expected_5d_net_pct"),
            "expected_21d_pct":  r.get("expected_21d_pct"),
            "why":               r["why"],
            "key_drivers":       r["key_drivers"],
            "key_risks":         r["key_risks"],
            "tags":              r["tags"],
            "position_plan":     (plan.as_dict() if plan else None),
            "components":        r["components"],
        }
        ideas.append(entry)
    return {
        "count":           len(ideas),
        "ideas":           ideas,
        "summary":         (f"{len(ideas)} long candidates; top conviction: "
                            + (ideas[0]["symbol"] if ideas else "none")),
    }


def _short_ideas(scored: list[dict], macro: dict,
                  briefing: dict) -> dict:
    """Top short candidates with risk-aware filters.

    In a NORMAL/AGGRESSIVE regime we filter out any short whose
    sector has a positive macro tilt — those shorts fight the tape.
    """
    sector_tilts = macro.get("sector_tilts") or {}
    regime = macro.get("risk_regime", "NORMAL")

    # Only consider names with score < -0.20 as legitimate shorts.
    # Below -0.10 alone is "weak hold" not "short candidate".
    raw_shorts = sorted(scored, key=lambda r: r["score"])[:10]
    filtered: list[dict] = []
    for r in raw_shorts:
        if r["score"] >= -0.20:
            break
        tilt = sector_tilts.get(r["sector"], 0)
        is_aggressive_regime = regime in ("AGGRESSIVE", "NORMAL")
        if tilt > 1 and is_aggressive_regime:
            # don't short sectors that have positive macro tilt in
            # a risk-on regime — that's fighting the tape
            continue
        filtered.append({
            "symbol":      r["symbol"],
            "sector":      r["sector"],
            "score":       r["score"],
            "conviction":  r["conviction"],
            "why":         r["why"],
            "key_drivers": r["key_drivers"],
            "key_risks":   r["key_risks"],
            "tags":        r["tags"],
            "macro_sector_tilt": tilt,
        })

    if not filtered:
        if regime == "AGGRESSIVE":
            note = "No shorts in AGGRESSIVE regime — bull tape dominates."
        else:
            note = ("No high-conviction shorts (score must be <= -0.20 "
                    "and not in a positive-tilt sector).")
    else:
        note = (f"{len(filtered)} short candidate(s); pre-filtered to drop "
                "names in supportive macro sectors.")

    return {
        "count":   len(filtered),
        "ideas":   filtered,
        "regime":  regime,
        "note":    note,
    }


def _portfolio_review(scored: list[dict], briefing: dict,
                       macro: dict, account_size_pkr: float | None = None) -> dict:
    """Per-holding action recommendation. Reads briefing.portfolio if present;
    falls back to an empty review if no portfolio loaded."""
    from brain.risk.stop_loss import compute_position_plan

    pf = briefing.get("portfolio") or briefing.get("positions") or []
    if isinstance(pf, dict):
        # Try to extract positions list
        pf = pf.get("positions") or pf.get("holdings") or []
    if not isinstance(pf, list):
        pf = []

    by_sym = {r["symbol"]: r for r in scored}
    rows = []
    for p in pf:
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol") or p.get("ticker")
        if not sym:
            continue
        scoring = by_sym.get(sym, {})
        score = scoring.get("score", 0.0)
        sector = scoring.get("sector") or p.get("sector", "")
        cost = p.get("avg_cost") or p.get("entry") or p.get("buy_price")
        qty = p.get("qty") or p.get("quantity") or p.get("shares")

        # Pull current price from predictions / ranking
        cur = scoring.get("entry_price")
        if cur is None:
            cur = p.get("current_price") or p.get("last_price")

        # P&L
        pnl_pct = None
        if cur is not None and cost is not None and cost > 0:
            pnl_pct = (float(cur) - float(cost)) / float(cost) * 100

        # Recommendation logic
        if score >= 0.30:
            action = "HOLD"   # already in; positive
            urgency = "LOW"
            note = "Score supports the position — hold."
        elif score >= 0.10:
            action = "HOLD"
            urgency = "LOW"
            note = "Mildly positive — hold but keep stop tight."
        elif score >= -0.10:
            action = "HOLD"
            urgency = "MEDIUM"
            note = "Mixed signals — set hard stop, no new buys."
        elif score >= -0.30:
            action = "TRIM"
            urgency = "MEDIUM"
            note = "Score has decayed — trim to half size."
        else:
            action = "EXIT"
            urgency = "HIGH"
            note = "Score strongly negative — exit on next strength."

        # Stop-loss plan from current price (not from cost)
        plan = compute_position_plan(
            symbol=sym, sector=sector,
            entry_price=cur,
            trade_style="swing",
            account_size_pkr=account_size_pkr,
        )

        rows.append({
            "symbol":            sym,
            "sector":            sector,
            "qty":               qty,
            "avg_cost":          cost,
            "current_price":     cur,
            "pnl_pct":           round(pnl_pct, 2) if pnl_pct is not None else None,
            "action":            action,
            "urgency":           urgency,
            "note":              note,
            "score":             score,
            "stop_loss_price":   (plan.stop_loss_price if plan else None),
            "stop_loss_pct":     (round(plan.stop_loss_pct * 100, 2) if plan else None),
            "target_price":      (plan.target_price if plan else None),
            "why":               scoring.get("why", ""),
        })

    return {
        "n_holdings":    len(rows),
        "holdings":      rows,
        "urgent_actions": [r for r in rows if r["urgency"] == "HIGH"],
    }


def _sector_view(scored: list[dict], macro: dict, briefing: dict) -> dict:
    """Outlook per sector with key drivers and top names."""
    sector_tilts = macro.get("sector_tilts") or {}
    by_sector: dict[str, list[dict]] = {}
    for r in scored:
        by_sector.setdefault(r["sector"], []).append(r)

    out = []
    for sector, rows in by_sector.items():
        if not sector:
            continue
        rows_sorted = sorted(rows, key=lambda r: -r["score"])
        tilt = sector_tilts.get(sector, 0)
        avg_score = sum(r["score"] for r in rows) / max(1, len(rows))
        if tilt >= 3 and avg_score >= 0.15:
            outlook = "BULLISH"
        elif tilt <= -2 or avg_score <= -0.15:
            outlook = "BEARISH"
        elif tilt >= 1:
            outlook = "POSITIVE"
        elif tilt <= -1:
            outlook = "NEGATIVE"
        else:
            outlook = "NEUTRAL"

        out.append({
            "sector":       sector,
            "outlook":      outlook,
            "macro_tilt":   tilt,
            "avg_score":    round(avg_score, 3),
            "n_stocks":     len(rows),
            "top_name":     rows_sorted[0]["symbol"] if rows_sorted else None,
            "top_score":    rows_sorted[0]["score"] if rows_sorted else None,
            "bottom_name":  rows_sorted[-1]["symbol"] if rows_sorted else None,
            "bottom_score": rows_sorted[-1]["score"] if rows_sorted else None,
        })
    out.sort(key=lambda s: -s["avg_score"])
    return {
        "sectors":   out,
        "best_sector":  out[0]["sector"] if out else None,
        "worst_sector": out[-1]["sector"] if out else None,
    }


def _watchlist(scored: list[dict], macro: dict) -> dict:
    """Names not yet actionable but worth watching for triggers."""
    # Borderline names: 0.05 <= score <= 0.30 (good signals but not enough)
    candidates = [r for r in scored if 0.05 <= r["score"] < 0.30][:8]
    out = []
    for r in candidates:
        triggers = []
        # What would push this to BUY territory?
        comp = r["components"]
        if comp["momentum"] < 0.2:
            triggers.append("Wait for 5d close above 20-SMA (momentum rebuild)")
        if comp["volume"] < 0.2:
            triggers.append("Wait for volume breakout (>1.5x 20d avg)")
        if comp["macro"] < 0.3:
            triggers.append("Wait for sector macro tilt to firm")
        out.append({
            "symbol":   r["symbol"],
            "sector":   r["sector"],
            "score":    r["score"],
            "triggers_to_buy": triggers[:3],
            "why":      r["why"],
        })
    return {
        "count":   len(out),
        "names":   out,
    }


def _risks_today(macro: dict, scored: list[dict], briefing: dict) -> dict:
    """Top 3-5 risks to today's positioning."""
    risks: list[dict] = []

    # 1) Risk-off macro drivers
    bear_tags = {"rate_up", "pkr_weak", "fx_blowout",
                 "oil_demand_destruction", "btc_risk_off",
                 "circular_debt_worsening", "imf_program_off_track"}
    for d in (macro.get("dominant_drivers") or []):
        if d["tag"] in bear_tags:
            risks.append({
                "type":  "macro",
                "tag":   d["tag"],
                "magnitude": d["magnitude"],
                "note":  f"{d['tag']} active — see macro tab."
            })

    # 2) Event proximity (de-risk window)
    ev = macro.get("key_event_window")
    if ev and ev.get("days_to") is not None and ev["days_to"] <= 3:
        risks.append({
            "type":  "event",
            "name":  ev["name"],
            "days_to": ev["days_to"],
            "note":  f"Event in {ev['days_to']}d — de-risk if <= 2d.",
        })

    # 3) Crowded sector concentration in longs
    longs = [r for r in scored if r["score"] >= 0.30]
    if longs:
        sector_counts: dict[str, int] = {}
        for r in longs:
            sector_counts[r["sector"]] = sector_counts.get(r["sector"], 0) + 1
        if sector_counts:
            top_sec, top_n = max(sector_counts.items(), key=lambda kv: kv[1])
            if top_n >= max(3, int(len(longs) * 0.5)):
                risks.append({
                    "type":  "concentration",
                    "sector": top_sec,
                    "n":     top_n,
                    "note":  f"{top_n}/{len(longs)} longs in {top_sec} — sector concentration risk.",
                })

    # 4) Cash adequacy
    regime = macro.get("risk_regime")
    if regime in ("CAUTIOUS", "DEFENSIVE"):
        risks.append({
            "type":  "stance",
            "note":  f"Stance is {regime} — verify cash >= 25% of NAV.",
        })

    # 5) Vol regime
    tape = macro.get("tape_state") or {}
    if tape.get("vol_regime") in ("ELEVATED", "CRISIS"):
        risks.append({
            "type":  "vol",
            "note":  f"Vol regime is {tape['vol_regime']} — use tighter stops "
                     "or smaller size.",
        })

    return {
        "count":  len(risks),
        "risks":  risks[:5],
    }


def _events_intelligence(briefing: dict, macro: dict,
                          scored: list[dict]) -> dict:
    """For each upcoming event, prepare a structured reaction plan."""
    upcoming = briefing.get("upcoming_events") or briefing.get("events") or []
    out = []
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
        if d_to < 0 or d_to > 30:
            continue
        name = str(ev.get("name") or ev.get("key") or "event")

        if d_to <= 2:
            plan_text = (f"De-risk window — within {d_to}d of {name}. "
                          "Trim size by 50% on volatile names; "
                          "hold cash buffer >= 25%.")
        elif d_to <= 7:
            plan_text = (f"{name} in {d_to}d — set tight stops on "
                          "event-sensitive names; do not add aggressive size.")
        else:
            plan_text = (f"{name} in {d_to}d — monitor; no action needed yet.")

        out.append({
            "name":     name,
            "days_to":  d_to,
            "category": ev.get("category") or ev.get("type"),
            "reaction_plan": plan_text,
        })
    out.sort(key=lambda e: e["days_to"])
    return {
        "count":   len(out),
        "events":  out,
    }


# ---------------------------------------------------------------------------
# Top-level result class
# ---------------------------------------------------------------------------
@dataclass
class MasterDecisionV2:
    as_of: str
    headline: str
    regime: str
    regime_confidence: str

    today_brief: dict
    long_ideas: dict
    short_ideas: dict
    portfolio_review: dict
    sector_view: dict
    watchlist: dict
    risks_today: dict
    events_intelligence: dict

    # source agent outputs (for audit + UI debugging)
    macro_summary: dict
    stock_scores: list[dict]

    # actions list compatible with legacy MasterDecision schema (UI back-compat)
    actions: list[dict]

    fallback_used: bool
    model: str
    narrative: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of":               self.as_of,
            "headline":            self.headline,
            "regime":              self.regime,
            "regime_confidence":   self.regime_confidence,
            "today_brief":         self.today_brief,
            "long_ideas":          self.long_ideas,
            "short_ideas":         self.short_ideas,
            "portfolio_review":    self.portfolio_review,
            "sector_view":         self.sector_view,
            "watchlist":           self.watchlist,
            "risks_today":         self.risks_today,
            "events_intelligence": self.events_intelligence,
            "macro_summary":       self.macro_summary,
            "stock_scores":        self.stock_scores,
            "actions":             self.actions,
            "fallback_used":       self.fallback_used,
            "model":               self.model,
            "narrative":           self.narrative,
            "version":             "v2",
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def decide_v2(briefing: dict,
              macro: dict | None = None,
              scored: list[dict] | None = None,
              *,
              account_size_pkr: float | None = None,
              use_llm: bool = False) -> MasterDecisionV2:
    """Run Agent C. Builds the multi-tab decision deterministically,
    optionally refining the narrative via Claude (does NOT change
    actions or stops)."""
    from brain.agents.macro_reader import read_macro
    from brain.agents.stock_scorer import score_universe

    if macro is None:
        macro = read_macro(briefing, use_llm=use_llm)
    if scored is None:
        scored = score_universe(briefing, macro_summary=macro, use_llm=use_llm)

    today        = _today_brief(macro, scored, briefing)
    longs        = _long_ideas(scored, briefing, account_size_pkr=account_size_pkr)
    shorts       = _short_ideas(scored, macro, briefing)
    portfolio    = _portfolio_review(scored, briefing, macro,
                                       account_size_pkr=account_size_pkr)
    sector_view  = _sector_view(scored, macro, briefing)
    watchlist    = _watchlist(scored, macro)
    risks        = _risks_today(macro, scored, briefing)
    events       = _events_intelligence(briefing, macro, scored)

    # Build a legacy-compatible actions list for the existing Today tab
    actions = []
    for idea in longs["ideas"][:5]:
        actions.append({
            "symbol":             idea["symbol"],
            "bucket":             idea["action"],
            "conviction":         idea["conviction"],
            "sector":             idea["sector"],
            "target_weight_pct":  (idea["position_plan"] or {}).get("position_size_pct"),
            "reason":             idea["why"],
            "contributing_signals": idea["key_drivers"],
            "position_plan":      idea["position_plan"],
        })
    for r in portfolio["urgent_actions"]:
        actions.append({
            "symbol":     r["symbol"],
            "bucket":     r["action"],
            "conviction": "HIGH",
            "sector":     r["sector"],
            "reason":     r["note"],
            "contributing_signals": [r["why"]],
        })

    # fallback_used = True unless we actually call Claude below. The
    # macro_summary's own fallback_used reflects whether Agent A called
    # Claude — but here in Agent C we only set fallback=False once we
    # actually run the LLM refinement at the bottom of this function.
    fallback_used = True

    decision = MasterDecisionV2(
        as_of=datetime.now().strftime("%Y-%m-%d"),
        headline=today["headline"],
        regime=today["regime"],
        regime_confidence=today["regime_confidence"] or "LOW",
        today_brief=today,
        long_ideas=longs,
        short_ideas=shorts,
        portfolio_review=portfolio,
        sector_view=sector_view,
        watchlist=watchlist,
        risks_today=risks,
        events_intelligence=events,
        macro_summary=macro,
        stock_scores=scored,
        actions=actions,
        fallback_used=fallback_used,
        model=("rule-based-v2" if fallback_used else "claude-sonnet-4-5"),
        narrative=macro.get("narrative", ""),
    )

    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            _refine_narratives_with_llm(decision, briefing)
        except Exception as e:
            decision.narrative = (decision.narrative
                                  + f"\n\n(LLM refinement skipped: {e})")

    return decision


def _refine_narratives_with_llm(decision: MasterDecisionV2,
                                  briefing: dict) -> None:
    """Optional Claude pass that ONLY enriches narrative text.
    Cannot change actions, stops, scores, or regime."""
    from ui.llm_clients import ClaudeClient, MASTER_STRATEGIST_MODEL
    import json as _json

    # Send only the structured digests (NOT the full briefing) to keep
    # input size small + reliable.
    digest = {
        "macro_summary":   decision.macro_summary,
        "top_5_longs":     decision.long_ideas["ideas"][:5],
        "top_3_shorts":    decision.short_ideas["ideas"][:3],
        "sector_outlook":  decision.sector_view["sectors"][:6],
        "risks_today":     decision.risks_today,
        "events":          decision.events_intelligence["events"][:3],
        "portfolio_urgent": decision.portfolio_review["urgent_actions"],
    }
    payload = _json.dumps(digest, default=str, indent=2)[:25_000]

    sys_prompt = (
        "You are the Master Strategist for a PSX trading bot. The "
        "user has already computed all actions, stops, scores, and "
        "regime classifications deterministically. Your ONLY job is "
        "to write a high-signal 4-6 sentence narrative paragraph "
        "explaining today's positioning. Stick to the facts in the "
        "digest. Do NOT propose new actions or change stops. "
        "Output JSON only: {\"narrative\": \"...\"}"
    )
    history = [{"role": "user",
                "content": "Digest:\n```json\n" + payload + "\n```\n\n"
                           "Return the JSON object with one narrative."}]
    client = ClaudeClient(model=MASTER_STRATEGIST_MODEL)
    result = client.run_chat(history=history, system=sys_prompt,
                              max_tokens=1_500, thinking_budget=1024,
                              max_tool_iterations=0)
    text = (result.get("final_text") or "").strip()
    if "{" in text:
        try:
            start = text.index("{")
            end   = text.rindex("}") + 1
            obj = _json.loads(text[start:end])
            if obj.get("narrative"):
                decision.narrative = obj["narrative"]
                decision.fallback_used = False
                decision.model = "claude-sonnet-4-5"
        except (ValueError, _json.JSONDecodeError):
            pass


if __name__ == "__main__":
    import json
    from pathlib import Path
    p = sorted(Path("data/_strategist").glob("_briefing_*.json"))[-1]
    briefing = json.loads(p.read_text(encoding="utf-8"))
    decision = decide_v2(briefing, account_size_pkr=1_000_000, use_llm=False)
    d = decision.as_dict()

    print("=" * 80)
    print(f"HEADLINE: {d['headline']}")
    print(f"REGIME:   {d['regime']} ({d['regime_confidence']})")
    print(f"MODEL:    {d['model']}")
    print()
    print("--- TODAY BRIEF ---")
    for b in d['today_brief']['bullets']:
        print(f"  • {b}")
    print()
    print(f"--- LONG IDEAS ({d['long_ideas']['count']}) ---")
    for idea in d['long_ideas']['ideas'][:5]:
        pp = idea['position_plan'] or {}
        print(f"  {idea['symbol']:<7} [{idea['action']:<4} {idea['conviction']:<7}] "
              f"score {idea['score']:+.2f}  "
              f"entry={pp.get('entry_price')}  "
              f"stop={pp.get('stop_loss_pct')}%  "
              f"target={pp.get('target_pct')}%  "
              f"size={pp.get('position_size_pct')}%")
        print(f"      why: {idea['why'][:90]}")
    print()
    print(f"--- SHORT IDEAS ({d['short_ideas']['count']}) ---")
    print(f"  note: {d['short_ideas']['note']}")
    for idea in d['short_ideas']['ideas'][:3]:
        print(f"  {idea['symbol']:<7} score {idea['score']:+.2f}  "
              f"sector_tilt={idea['macro_sector_tilt']}  why: {idea['why'][:60]}")
    print()
    print(f"--- SECTOR VIEW (top 5) ---")
    for s in d['sector_view']['sectors'][:5]:
        print(f"  {s['sector']:<22} {s['outlook']:<10} "
              f"tilt={s['macro_tilt']:+d} avg={s['avg_score']:+.2f} "
              f"top={s['top_name']} ({s['top_score']:+.2f})")
    print()
    print(f"--- WATCHLIST ({d['watchlist']['count']}) ---")
    for w in d['watchlist']['names'][:5]:
        print(f"  {w['symbol']:<7} score {w['score']:+.2f}  triggers: "
              f"{'; '.join(w['triggers_to_buy'][:2])}")
    print()
    print(f"--- RISKS TODAY ({d['risks_today']['count']}) ---")
    for r in d['risks_today']['risks']:
        print(f"  [{r['type']}] {r['note']}")
    print()
    print(f"--- EVENTS INTELLIGENCE ({d['events_intelligence']['count']}) ---")
    for e in d['events_intelligence']['events'][:3]:
        print(f"  {e['name']:<40} in {e['days_to']}d: {e['reaction_plan'][:80]}")
    print()
    print(f"--- LEGACY ACTIONS ({len(d['actions'])}) ---")
    for a in d['actions']:
        pp = a.get('position_plan') or {}
        print(f"  {a.get('symbol','-'):<7} {a['bucket']:<5} "
              f"({a['conviction']:<7}) sector={a.get('sector','')} "
              f"size={pp.get('position_size_pct')}%")
