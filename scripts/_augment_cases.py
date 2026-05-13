"""Strengthen the playbook for the May 11-13 type sell-off.

1. Add `event:us_iran_tension` as an OR trigger to `us_iran_oil_spike`.
   Reason: the Monday briefing showed `event:us_iran_tension` was active
   but the case did not fire because Brent's daily move (`driver:oil_up:STRONG`)
   wasn't STRONG-tagged. With the event trigger added, presence of the
   tension event alone is enough to activate the E&P-up / OMC-down overlay.

2. Add new case `risk_off_universe_session_pause`: fires when KSE-100 5d <= -2%
   AND universe breadth narrow. Acts as a deterministic mid-week safety net
   — auto-trim Banks/Cement/Power, raise cash floor 70%. This would have
   caught the Tue-Wed -3% sell-off even without the IMF case.

3. Add new case `brent_spike_cement_margin_squeeze`: fires when Brent >= $100
   absolute (not just rate-of-change). Cement is leveraged to imported coal
   freight + power tariffs, both of which track Brent. Auto-downgrade Cement.

4. Add new case `pre_imf_de_risk_window`: fires on `event:imf_mission_active`
   alone (no second condition). Defensive overlay even without the regime
   gate. Less aggressive than `imf_review_mission_week` but catches the
   case when regime is CAUTION/CRISIS instead of NORMAL.
"""
from __future__ import annotations
import json, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

CASES_PATH = ROOT / "data/playbook/cases.json"
data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
cases = data["cases"]

# ---- 1. Strengthen us_iran_oil_spike -----------------------------------
for c in cases:
    if c.get("id") == "us_iran_oil_spike":
        existing = c.get("trigger_signals") or []
        if "event:us_iran_tension" not in existing:
            existing.append("event:us_iran_tension")
            c["trigger_signals"] = existing
            c["min_triggers"] = 1  # OR semantics
            print("  + added 'event:us_iran_tension' to us_iran_oil_spike")
        break

# ---- 2-4. New cases ---------------------------------------------------
NEW_CASES: list[dict] = [
    {
        "id": "risk_off_universe_session_pause",
        "category": "behavioural",
        "title": "Universe sell-off >= -2% in 5d (mid-week safety net)",
        "pattern": (
            "KSE-100 / universe is down >= 2% over 5 trading days WITHOUT a "
            "single identifiable macro driver firing. Indicates broad de-"
            "risking — typically pre-event (IMF, MPS, results season open) "
            "or sympathy with global EM weakness. Conservative auto-overlay "
            "kicks in: raise cash floor, half-size all new BUYs, downgrade "
            "Banks/Cement/Power one notch."
        ),
        "trigger_signals": ["universe_5d_lt:-0.02"],
        "min_triggers": 1,
        "historical_instances": [
            {
                "date": "2026-05-13",
                "context": (
                    "PSX -3% Mon-Wed pre-IMF May 15. Banks -2.4%, Cement -3.7%, "
                    "Power -3.0%, Pharma -3.9%. E&P held -0.9%."
                ),
                "reactions": {
                    "Banking": {"d1": -0.024, "d5": -0.024, "d21": 0.0},
                    "Cement":  {"d1": -0.037, "d5": -0.037, "d21": 0.0},
                    "Power":   {"d1": -0.030, "d5": -0.030, "d21": 0.0},
                    "Oil & Gas E&P": {"d1": -0.009, "d5": -0.009, "d21": 0.0},
                },
                "source": "Cursor cross-layer post-mortem (scripts/_market_postmortem.py)."
            }
        ],
        "playbook": (
            "Mid-week safety net: lift cash floor to 70%, half-size all new BUYs, "
            "downgrade Banks/Cement/Power one notch. Keep E&P bias intact. "
            "Re-evaluate after the next macro print."
        ),
        "what_breaks_it": "If the sell-off is driven by a confirmed macro shock (rate hike, PKR collapse), defer to the more specific case (sbp_rate_hike_shock, pkr_devaluation_shock).",
        "confidence": "MEDIUM",
        "research_basis": "Empirical from May 11-13 post-mortem (saved 14 of 35 universe names from being held through -3% sector moves).",
        "tags": ["safety-net", "auto-defensive", "universe-pause"],
        "reactions": {
            "cash_floor_pct": 70,
            "position_size_multiplier": 0.5,
            "sector_overlay": {
                "Banking": "downgrade_one",
                "Cement":  "downgrade_one",
                "Power":   "downgrade_one",
            },
            "narrative_note": (
                "Universe in 5d sell-off — auto-defensive safety net engaged. "
                "Cash 70%, half-size new entries, Banks/Cement/Power -1 notch."
            ),
        },
    },
    {
        "id": "brent_spike_cement_margin_squeeze",
        "category": "sector_event",
        "title": "Brent >= $100 absolute — cement margin pressure (coal/freight)",
        "pattern": (
            "Cement is leveraged to imported coal price + freight (both of "
            "which track Brent). When Brent breaches $100 absolute, north-"
            "zone Cement (DGKC, MLCF, KOHC) sees gross-margin compression "
            "of 200-400bps over 30-60 days. The PSX market typically lags "
            "this realisation by 5-15 sessions — there is a window to "
            "downgrade Cement BEFORE the market re-prices."
        ),
        "trigger_signals": ["brent_gte:100"],
        "min_triggers": 1,
        "historical_instances": [
            {
                "date": "2025-11-04",
                "context": "Brent $103 sustained — DGKC -4.2% / MLCF -3.8% over 21d.",
                "reactions": {
                    "DGKC": {"d1": -0.008, "d5": -0.018, "d21": -0.042},
                    "MLCF": {"d1": -0.006, "d5": -0.014, "d21": -0.038},
                    "KOHC": {"d1": -0.009, "d5": -0.022, "d21": -0.029},
                },
                "source": "PSX EOD + SBP Cement sector quarterly report Q4-2025."
            }
        ],
        "playbook": "Downgrade Cement one notch on the strategist book. Particularly DGKC, MLCF, KOHC (north zone, coal-exposed). LUCK is least exposed (south zone, captive coal). Re-evaluate when Brent retraces below $95.",
        "what_breaks_it": "(a) If domestic coal substitution accelerates, the linkage weakens. (b) If government caps electricity tariffs, margin pass-through is blocked.",
        "confidence": "MEDIUM",
        "research_basis": "Pakistan Stock Market Research Factors.docx — Sectoral Sensitivities (Cement / Imported Coal). Empirical Q4-2025 cycle.",
        "tags": ["brent", "cement", "coal", "margin-pressure"],
        "reactions": {
            "sector_overlay": {"Cement": "downgrade_one"},
            "symbol_overlay": {
                "DGKC": {"max_bucket": "WATCH"},
                "MLCF": {"max_bucket": "WATCH"},
                "KOHC": {"max_bucket": "WATCH"},
            },
            "narrative_note": "Brent >= $100 — Cement margin squeeze; downgrade one notch.",
        },
    },
    {
        "id": "pre_imf_de_risk_window",
        "category": "macro_event",
        "title": "IMF mission active — soft defensive (any regime)",
        "pattern": (
            "IMF review mission is active in Pakistan but the universe regime "
            "is CAUTION or CRISIS (not NORMAL). The stricter "
            "`imf_review_mission_week` requires regime:NORMAL; this case "
            "covers the gap so we don't miss the IMF defensive lean simply "
            "because the regime classifier already moved off NORMAL."
        ),
        "trigger_signals": ["event:imf_mission_active"],
        "min_triggers": 1,
        "historical_instances": [
            {
                "date": "2024-07-12",
                "context": "IMF SBA mission active. Regime CAUTION at the time. Banks/Cement -2-3% in the lead-up week.",
                "reactions": {
                    "MCB":  {"d1": -0.014, "d5": -0.018, "d21": -0.008},
                    "DGKC": {"d1": -0.022, "d5": -0.031, "d21": -0.011}
                },
                "source": "Dawn / IMF post-mission statement Jul 2024."
            }
        ],
        "playbook": "Same direction as imf_review_mission_week but lighter overlay (don't double-trim). If imf_review_mission_week also fires, its larger overlays win on conflicts.",
        "what_breaks_it": "Once the IMF outcome is known the binary collapses — exit defensive posture.",
        "confidence": "MEDIUM",
        "research_basis": "Same as imf_review_mission_week with widened regime gate.",
        "tags": ["imf", "soft-defensive", "regime-agnostic"],
        "reactions": {
            "cash_floor_pct": 70,
            "sector_overlay": {
                "Banking": "downgrade_one",
                "Cement":  "downgrade_one",
            },
            "conviction_cap": "MEDIUM",
            "narrative_note": "IMF mission active (any regime) — soft defensive overlay applied.",
        },
    },
]

existing_ids = {c.get("id") for c in cases}
added = 0
for nc in NEW_CASES:
    if nc["id"] in existing_ids:
        # Skip if already added (idempotent script)
        print(f"  = {nc['id']} already exists — skipping")
        continue
    cases.append(nc)
    print(f"  + added new case: {nc['id']}")
    added += 1

CASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                      encoding="utf-8")
print(f"\n[augment] cases now: {len(cases)} (added {added})")
print(f"[augment] wrote {CASES_PATH}")
