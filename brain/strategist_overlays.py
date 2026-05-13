"""Deterministic playbook-driven overlays applied AFTER the strategist
(LLM or fallback) produces actions.

Built 2026-05-13 after the May 11-13 sell-off where:
  - playbook fired `imf_review_mission_week` correctly Monday (score 2.6)
  - LLM was offline (Anthropic credits exhausted)
  - fallback strategist did not translate the fired case into actions
  - banks/cement/power got crushed -2.4 to -3.7% as HOLDs

This module reads `briefing.playbook_analogues` (list of fired cases) and
their `reactions` block from `data/playbook/cases.json`, then mutates the
decision dict in place. Operates on the decision-as-dict (post `.as_dict()`)
so it is independent of the LLM/fallback path that produced it.

Bucket order (low to high conviction):
  AVOID < TRIM < WATCH < HOLD < ADD < BUY

`downgrade_one`: each per-symbol bucket in the named sector shifts ONE notch down.
`upgrade_one`:   shifts ONE notch up.

Cases are applied in `match_score` DESCENDING order so the highest-confidence
case wins on conflicts. The lower-score cases only fill gaps the stronger
case did not touch (idempotent under stable inputs).
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "data" / "playbook" / "cases.json"

BUCKET_RANK = {
    "AVOID": 0,
    "TRIM":  1,
    "WATCH": 2,
    "HOLD":  3,
    "ADD":   4,
    "BUY":   5,
}
RANK_BUCKET = {v: k for k, v in BUCKET_RANK.items()}

# Sectors are shown in actions[].sector. We normalise common variants.
SECTOR_ALIASES = {
    "oil & gas e&p": "Oil & Gas E&P",
    "oil_gas_eandp": "Oil & Gas E&P",
    "e&p":           "Oil & Gas E&P",
    "oil and gas":   "Oil & Gas E&P",
    "banking":       "Banking",
    "banks":         "Banking",
    "cement":        "Cement",
    "cements":       "Cement",
    "power":         "Power",
    "ipp":           "Power",
    "fertilizer":    "Fertilizer",
    "fertilizers":   "Fertilizer",
    "fert":          "Fertilizer",
    "omc":           "OMC",
    "refining":      "Refining",
    "refiner":       "Refining",
    "autos":         "Autos",
    "auto":          "Autos",
    "consumer":      "Consumer",
    "conglomerate":  "Conglomerate",
    "technology":    "Technology",
    "tech":          "Technology",
    "pharma":        "Pharma",
    "pharmaceutical":"Pharma",
}


def _norm_sector(s: str | None) -> str:
    if not s:
        return ""
    head = s.split("/")[0].strip()
    return SECTOR_ALIASES.get(head.lower(), head)


def _shift_bucket(bucket: str, delta: int) -> str:
    rank = BUCKET_RANK.get((bucket or "HOLD").upper(), 3)
    new_rank = max(0, min(5, rank + delta))
    return RANK_BUCKET[new_rank]


def _clamp_bucket(bucket: str,
                  min_b: str | None = None,
                  max_b: str | None = None) -> str:
    rank = BUCKET_RANK.get((bucket or "HOLD").upper(), 3)
    if min_b is not None:
        rank = max(rank, BUCKET_RANK.get(min_b.upper(), rank))
    if max_b is not None:
        rank = min(rank, BUCKET_RANK.get(max_b.upper(), rank))
    return RANK_BUCKET[rank]


def _load_case_reactions() -> dict[str, dict]:
    """Load {case_id: reactions} from the raw cases.json."""
    if not CASES_PATH.exists():
        return {}
    try:
        data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict] = {}
    for c in data.get("cases", []):
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        rx = c.get("reactions")
        if cid and isinstance(rx, dict):
            out[cid] = rx
    return out


def _fired_cases(briefing: dict) -> list[dict]:
    """Return fired cases sorted by match_score desc."""
    pb = briefing.get("playbook_analogues") or []
    if isinstance(pb, dict):
        # Some briefings serialise as dict keyed by case id
        pb = [{"id": k, **(v if isinstance(v, dict) else {})}
              for k, v in pb.items()]
    fired = [c for c in pb
             if isinstance(c, dict)
             and (c.get("match_score") or 0) > 0
             and c.get("id")]
    fired.sort(key=lambda c: c.get("match_score") or 0, reverse=True)
    return fired


def apply_playbook_overlays(decision: dict, briefing: dict) -> dict:
    """Mutate `decision` in place. Returns the same dict for chaining.

    Adds a `playbook_overlay_log` field to the decision listing the changes
    actually made (so the UI / audit can show them).
    """
    fired = _fired_cases(briefing)
    if not fired:
        decision.setdefault("playbook_overlay_log", [])
        return decision

    case_rx = _load_case_reactions()
    if not case_rx:
        decision.setdefault("playbook_overlay_log", [])
        return decision

    actions: list[dict] = list(decision.get("actions") or [])
    if not actions:
        decision.setdefault("playbook_overlay_log", [])
        return decision

    log: list[dict] = []
    overlay_notes: list[str] = []

    # Track which symbols a stronger (earlier) case has already touched, so
    # weaker cases don't re-overwrite them.
    sym_touched_min: dict[str, int] = {}
    sym_touched_max: dict[str, int] = {}

    cash_floor = 0.0
    pos_size_mult = 1.0
    conviction_cap_rank: int | None = None
    CONV_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    CONV_NAME = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}

    for case in fired:
        cid = case.get("id")
        rx = case_rx.get(cid)
        if not rx:
            continue

        case_log: dict[str, Any] = {
            "case_id": cid,
            "match_score": case.get("match_score"),
            "fired_triggers": case.get("fired_triggers"),
            "changes": [],
        }

        # 1. Sector overlay
        sec_overlay = rx.get("sector_overlay") or {}
        for sec_raw, action in sec_overlay.items():
            target_sec = _norm_sector(sec_raw)
            if not target_sec or action not in ("downgrade_one", "upgrade_one"):
                continue
            delta = -1 if action == "downgrade_one" else +1
            for a in actions:
                if not a.get("symbol"):
                    continue
                if _norm_sector(a.get("sector")) != target_sec:
                    continue
                old_b = (a.get("bucket") or "HOLD").upper()
                new_b = _shift_bucket(old_b, delta)
                if new_b != old_b:
                    a["bucket"] = new_b
                    a.setdefault("contributing_signals", []).append(
                        f"playbook:{cid}:sector_overlay:{action}")
                    case_log["changes"].append({
                        "symbol": a["symbol"], "from": old_b, "to": new_b,
                        "via": f"sector_overlay:{target_sec}:{action}"
                    })

        # 2. Symbol overlay (clamp min/max + weight floor)
        sym_overlay = rx.get("symbol_overlay") or {}
        for sym, params in sym_overlay.items():
            if not isinstance(params, dict):
                continue
            min_b = params.get("min_bucket")
            max_b = params.get("max_bucket")
            wf = params.get("weight_floor_pct")

            min_rank = BUCKET_RANK.get(min_b.upper(), -1) if min_b else -1
            max_rank = BUCKET_RANK.get(max_b.upper(), 99) if max_b else 99

            # Stronger case already wins; check sym_touched
            existing_min = sym_touched_min.get(sym, -1)
            existing_max = sym_touched_max.get(sym, 99)
            if min_rank <= existing_min and max_rank >= existing_max and wf is None:
                continue

            for a in actions:
                if a.get("symbol") != sym:
                    continue
                old_b = (a.get("bucket") or "HOLD").upper()
                new_b = _clamp_bucket(old_b, min_b, max_b)
                if new_b != old_b:
                    a["bucket"] = new_b
                    a.setdefault("contributing_signals", []).append(
                        f"playbook:{cid}:symbol_clamp")
                    case_log["changes"].append({
                        "symbol": sym, "from": old_b, "to": new_b,
                        "via": f"symbol_clamp(min={min_b},max={max_b})"
                    })

                # Weight floor (only matters if bucket implies long exposure)
                if wf is not None and (a.get("target_weight_pct") or 0) < wf:
                    if (a.get("bucket") or "").upper() in ("BUY", "ADD", "HOLD"):
                        old_w = a.get("target_weight_pct")
                        a["target_weight_pct"] = float(wf)
                        case_log["changes"].append({
                            "symbol": sym, "weight_from": old_w, "weight_to": wf,
                            "via": "weight_floor"
                        })

            if min_rank > existing_min:
                sym_touched_min[sym] = min_rank
            if max_rank < existing_max:
                sym_touched_max[sym] = max_rank

        # 3. Cash floor (we keep the MAX across all fired cases)
        cf = rx.get("cash_floor_pct")
        if cf is not None and float(cf) > cash_floor:
            cash_floor = float(cf)
            case_log["changes"].append({"cash_floor_pct": cf,
                                        "via": "cash_floor"})

        # 4. Position size multiplier (we keep the MIN — most restrictive wins)
        psm = rx.get("position_size_multiplier")
        if psm is not None and float(psm) < pos_size_mult:
            pos_size_mult = float(psm)
            case_log["changes"].append({"position_size_multiplier": psm,
                                        "via": "size_haircut"})

        # 5. Conviction cap (we keep the LOWEST cap across cases)
        cc = rx.get("conviction_cap")
        if cc is not None:
            cc_rank = CONV_RANK.get(cc.upper(), 1)
            if conviction_cap_rank is None or cc_rank < conviction_cap_rank:
                conviction_cap_rank = cc_rank
                case_log["changes"].append({"conviction_cap": cc.upper(),
                                            "via": "conviction_cap"})

        # 6. Narrative note
        note = rx.get("narrative_note")
        if note:
            overlay_notes.append(f"[{cid}] {note}")

        if case_log["changes"] or note:
            log.append(case_log)

    # Apply cash_floor to the CASH action (or insert one).
    if cash_floor > 0:
        cash_action = next(
            (a for a in actions
             if (a.get("bucket") or "").upper() == "CASH"
             and not a.get("symbol")), None)
        if cash_action is None:
            cash_action = {
                "symbol": None, "sector": None, "bucket": "CASH",
                "conviction": "MEDIUM", "target_weight_pct": cash_floor,
                "reason": "Auto-inserted by playbook overlay (cash floor).",
                "contributing_signals": [],
            }
            actions.insert(0, cash_action)
        old_w = cash_action.get("target_weight_pct") or 0
        if old_w < cash_floor:
            cash_action["target_weight_pct"] = cash_floor
            cash_action["reason"] = (
                (cash_action.get("reason") or "")[:200]
                + f" | playbook cash_floor raised to {cash_floor}%."
            )

    # Apply position-size haircut to all BUY/ADD non-CASH actions.
    if pos_size_mult < 1.0:
        for a in actions:
            if a.get("symbol") and (a.get("bucket") or "").upper() in ("BUY", "ADD"):
                w = a.get("target_weight_pct")
                if w and w > 0:
                    new_w = round(w * pos_size_mult, 2)
                    a["target_weight_pct"] = new_w

    # Apply conviction cap.
    if conviction_cap_rank is not None:
        cap_name = CONV_NAME[conviction_cap_rank]
        for a in actions:
            if not a.get("conviction"):
                continue
            cur = CONV_RANK.get((a.get("conviction") or "MEDIUM").upper(), 1)
            if cur > conviction_cap_rank:
                a["conviction"] = cap_name
        # Also cap top-level decision conviction
        top_cur = CONV_RANK.get((decision.get("conviction") or "MEDIUM").upper(), 1)
        if top_cur > conviction_cap_rank:
            decision["conviction"] = cap_name

    # Persist mutations
    decision["actions"] = actions
    decision["playbook_overlay_log"] = log
    if overlay_notes:
        nb = decision.get("narrative") or ""
        joined = " || ".join(overlay_notes)
        decision["narrative"] = (nb + "\n\n[Playbook overlay] " + joined)[:4000]
        # Also expose for the UI as a structured field
        decision["playbook_overlay_notes"] = overlay_notes

    return decision


def overlay_summary(decision: dict) -> str:
    """Short human-readable summary of the overlay log (for logs / health)."""
    log = decision.get("playbook_overlay_log") or []
    if not log:
        return "no playbook overlays applied"
    lines = []
    for case in log:
        lines.append(f"  - {case['case_id']} (score {case['match_score']}): "
                     f"{len(case['changes'])} change(s)")
        for ch in case["changes"][:6]:
            lines.append(f"      * {ch}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Predictor-side helper: convert fired playbook cases to a bias dict the
# rule-based predictor can apply during scoring (so per-stock predictions
# pick up sector tilts even when the strategist isn't run for that pass).
# ---------------------------------------------------------------------------
# Bucket-shift to score-bias mapping.
# ONE bucket notch downgrade ~ 0.15 score delta (in [-1, +1] space).
# Strong symbol overrides (max_bucket=AVOID) stack via the score floor.
_NOTCH_BIAS = 0.15


def compute_predictor_bias(briefing: dict) -> dict:
    """Return {sector_bias: {sector: float}, symbol_bias: {sym: float}}.

    Reads fired playbook cases + their reactions from the briefing and
    converts the deterministic action overlays into score nudges the
    rule-based predictor can stack on top of its technical signal.

    Designed to be safe to call when no cases fire (returns empty dicts).
    """
    fired = _fired_cases(briefing)
    case_rx = _load_case_reactions()
    sector_bias: dict[str, float] = {}
    symbol_bias: dict[str, float] = {}

    for case in fired:
        rx = case_rx.get(case.get("id"))
        if not rx:
            continue
        for sec_raw, action in (rx.get("sector_overlay") or {}).items():
            sec = _norm_sector(sec_raw)
            if not sec:
                continue
            delta = -_NOTCH_BIAS if action == "downgrade_one" else (
                +_NOTCH_BIAS if action == "upgrade_one" else 0.0)
            sector_bias[sec] = sector_bias.get(sec, 0.0) + delta

        for sym, params in (rx.get("symbol_overlay") or {}).items():
            if not isinstance(params, dict):
                continue
            min_b = params.get("min_bucket")
            max_b = params.get("max_bucket")
            # min_bucket=ADD = bullish floor → +0.30 if currently NEUTRAL
            # max_bucket=AVOID = bearish ceiling → -0.30
            if max_b == "AVOID":
                symbol_bias[sym] = symbol_bias.get(sym, 0.0) - 0.30
            elif max_b == "TRIM":
                symbol_bias[sym] = symbol_bias.get(sym, 0.0) - 0.20
            elif max_b == "WATCH":
                symbol_bias[sym] = symbol_bias.get(sym, 0.0) - 0.15
            if min_b == "BUY":
                symbol_bias[sym] = symbol_bias.get(sym, 0.0) + 0.30
            elif min_b == "ADD":
                symbol_bias[sym] = symbol_bias.get(sym, 0.0) + 0.20
            elif min_b == "HOLD":
                symbol_bias[sym] = symbol_bias.get(sym, 0.0) + 0.05

    # Clamp totals to a reasonable range so cumulative biases can't dominate
    # the technical score (which itself maxes around +/- 1.0).
    sector_bias = {k: max(-0.40, min(0.40, v)) for k, v in sector_bias.items()}
    symbol_bias = {k: max(-0.50, min(0.50, v)) for k, v in symbol_bias.items()}
    return {
        "sector_bias": sector_bias,
        "symbol_bias": symbol_bias,
        "fired_case_ids": [c.get("id") for c in fired],
    }
