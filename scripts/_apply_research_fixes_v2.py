"""ROUND-2 fixes to the playbook based on the 5-yr per-sector move catalog.

After Round-1 (2026-05-13 morning) lifted the overlay edge from -44% to
+5.65%, the per-sector mining (`scripts/_mine_sector_moves.py`) revealed
several distinct event archetypes that either had NO playbook case or
were mis-handled by an existing case:

GAPS DISCOVERED (with example dates):

  G1. **Mega-rally on later-cycle cut (day 1-14)** — 2025-05-15 +16.34%
      universe (8th cut to 11%). Falls in the gap between
      `sbp_rate_cut_cycle_initiation` (1st cut only) and
      `post_cut_cycle_continuation` (>=14 days post-cut). NO case fires
      the explosive 5-15% first-week broad rally on later cuts.

  G2. **Oil-spike-as-systemic-risk-off** — 2026-03-09/17/31 saw
      `oil_up:STRONG` PLUS broad sell-off (universe -8%, E&P -8.86%,
      Cement -12.46%). Existing `brent_spike_e_and_p` UPGRADES E&P in
      this regime, which is exactly wrong. Needed: split case OR add a
      breadth qualifier to the upgrade trigger.

  G3. **Oil-demand-destruction risk-off** — 2021-11-26, 2023-05-10,
      2022-04-05 all saw `oil_down:MODERATE` paired with broad
      sell-off. NO case fires for this (oil_down currently bullish for
      Cement only). When oil falls due to global recession fears, EM
      equities take the hit too.

  G4. **Power crushed on IMF approval week** — 2024-09-27 Power
      -11.62%, 2024-10-16 Power -14.27% (with `imf_sba_or_eff_approval`
      event active). Our `imf_sba_eff_approval` case had no Power
      reaction. Mechanism: IMF programs typically demand power tariff
      hikes that hurt collection / cash flow.

  G5. **Cement crushed on STRONG PKR devaluation** — Round-1 dropped
      `pkr_devaluation_shock × Cement → downgrade_one` because aggregate
      accuracy was 41%. The catalog shows the reaction was right when
      `pkr_weak:STRONG` (sudden devaluation):
        2022-07-21 (STRONG) Cement -11.48%
        2022-06-13 (MOD/STRONG) Cement -6.66%
        2023-08-31 (MOD)    Cement -8.40%
      And wrong when `pkr_weak:MODERATE` (gradual). Tighten case to
      STRONG only and re-add Cement.

  G6. **`brent_spike_e_and_p` fires too freely** — currently any
      `oil_up` triggers it; backtest shows -0.21% per-fire edge / 49%
      accuracy. Add breadth + non-crisis guards so it only fires when
      the oil-up is a CLEAN supply-side shock, not part of broad
      risk-off.

  G7. **`us_iran_oil_spike × E&P upgrade` wrong in risk-off** — same
      mechanism as G2. The case fires correctly on the geopolitical
      event but the E&P upgrade is wrong direction when accompanying
      broad sell-off. Drop the upgrade in this case (the new G2 case
      handles the defensive side; user can still trade E&P discretely
      on event-only rationale).

Round-2 changes applied here:
  R1: NEW case `nth_rate_cut_immediate_window`
  R2: NEW case `oil_spike_systemic_risk_off`
  R3: NEW case `oil_demand_destruction_risk_off`
  R4: imf_sba_eff_approval — ADD Power downgrade
  R5: pkr_devaluation_shock — tighten trigger to STRONG; re-add Cement
  R6: brent_spike_e_and_p — add breadth+regime guards
  R7: us_iran_oil_spike — drop E&P sector upgrade
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")
CASES_PATH = ROOT / "data/playbook/cases.json"


def main() -> int:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases: list[dict] = data["cases"]
    by_id = {c.get("id"): c for c in cases if isinstance(c, dict)}
    log: list[str] = []

    # ---- R1: NEW nth_rate_cut_immediate_window -------------------------
    if "nth_rate_cut_immediate_window" not in by_id:
        new_case = {
            "id": "nth_rate_cut_immediate_window",
            "category": "macro_event",
            "title": "Nth rate cut (>=2nd) immediate-window mega-rally (days 1-14)",
            "pattern": (
                "When SBP delivers a cut that is NOT the first of the cycle "
                "AND the cut lands during a benign/normal regime, the first "
                "1-14 trading days frequently produce an explosive broad "
                "rally (5-16% on the universe equal-weighted index). The "
                "mechanism: investors front-run the policy transmission, "
                "leveraged sectors re-rate immediately, and short-covering "
                "amplifies the move. Distinct from `sbp_rate_cut_cycle_initiation` "
                "(first cut only, n=1) and `post_cut_cycle_continuation` "
                "(starts at day 14). Catches the 14-day blind spot for "
                "later cuts (e.g. the 2025-05-15 +16.34% mega-rally on the "
                "8th cut delivered 10 days prior). 2026-05-13 catalog: "
                "this single missing case was the largest source of "
                "uncaptured upside in the 5-year backtest."
            ),
            "trigger_signals": [
                "driver:rate_down",
                "days_since_last_cut_lte:14",
                "rate_cuts_180d_gte:2",
                "regime:NORMAL"
            ],
            "min_triggers": 4,
            "historical_instances": [
                {
                    "date": "2025-05-15",
                    "context": "8th cut of cycle (12% -> 11%) on 2025-05-05; +16.34% universe / +21% Cement / +29% OMC over the next 5 trading days.",
                    "reactions": {
                        "KSE100": {"d1": 0.024, "d5": 0.163, "d21": 0.023}
                    },
                    "source": "PSX EOD; SBP MPC press release 5-May-2025."
                },
                {
                    "date": "2024-12-19",
                    "context": "5th consecutive cut (15% -> 13%) on 2024-12-16; sharp -6.82% drawdown then +9.40% over 21 days (V-bounce).",
                    "reactions": {
                        "KSE100": {"d1": -0.022, "d5": -0.068, "d21": 0.094}
                    },
                    "source": "SBP MPC press release 16-Dec-2024; PSX EOD."
                }
            ],
            "playbook": (
                "ADD broadly across leveraged-cyclical sectors (Cement, "
                "Conglomerate/Chem, Power, OMC) with 14-day horizon. "
                "Avoid haircut on incumbent positions; let winners run. "
                "Don't re-fire `post_cut_cycle_continuation` overlays "
                "until day 14+. If the same week produces a sharp "
                "intraday drawdown, stay with the position — historical "
                "21d return is positive even when day-1 is sharply red "
                "(see 2024-12-19 instance above)."
            ),
            "what_breaks_it": (
                "A simultaneous PKR shock or Brent spike (>=US$ 105) "
                "neutralises the rally. Also broken if regime flips to "
                "CRISIS during the window (broad cross-sectional capitulation)."
            ),
            "confidence": "HIGH",
            "research_basis": (
                "5-year per-sector catalog mining (2026-05-13). Of the "
                "top-10 universe-wide UP moves over 2021-2026, 6 fell "
                "into this window (later-cut, days 1-14, NORMAL regime). "
                "Only 1 of those 6 produced any case fire under the "
                "pre-Round-2 playbook."
            ),
            "tags": ["rate-cut", "explosive-rally", "leveraged-sectors",
                     "first-2-weeks", "round-2-discovery"],
            "reactions": {
                "sector_overlay": {
                    "Cement":       "upgrade_one",
                    "Conglomerate": "upgrade_one",
                    "OMC":          "upgrade_one",
                    "Power":        "upgrade_one",
                    "Banking":      "upgrade_one"
                },
                "symbol_overlay": {
                    "MLCF":  {"min_bucket": "ADD"},
                    "FCCL":  {"min_bucket": "ADD"},
                    "KOHC":  {"min_bucket": "ADD"},
                    "EPCL":  {"min_bucket": "ADD"},
                    "ENGROH": {"min_bucket": "ADD"},
                    "HUBC":  {"min_bucket": "HOLD"},
                    "NPL":   {"min_bucket": "HOLD"}
                },
                "cash_floor_pct": 20,
                "narrative_note": (
                    "Nth-rate-cut mega-rally window (days 1-14) — broad "
                    "leveraged-cyclical lift; cash floor LOW (20%) to let "
                    "exposure work."
                )
            }
        }
        cases.append(new_case)
        log.append("R1: NEW case nth_rate_cut_immediate_window (catches 2025-05-15 +16% gap)")

    # ---- R2: NEW oil_spike_systemic_risk_off ---------------------------
    if "oil_spike_systemic_risk_off" not in by_id:
        new_case = {
            "id": "oil_spike_systemic_risk_off",
            "category": "macro_event",
            "title": "Oil spike PLUS broad sell-off (oil-up as systemic risk-off)",
            "pattern": (
                "When Brent spikes >=10% in 21d AND the universe equal-weighted "
                "index has fallen >=3% in 5 days AND breadth is below 30%, the "
                "oil rise is functioning as a SYSTEMIC RISK-OFF signal "
                "(geopolitical / inflation-shock / Fed hawkishness amplifier) "
                "rather than a pure commodity supply story. In this regime "
                "even E&P producers fall along with the broad market — the "
                "USD wellhead-pricing benefit is overwhelmed by the global "
                "risk-off discount applied to all PSX names. Discovered 2026-05-13 "
                "from per-sector mining: 2026-03-09 / 2026-03-17 / 2026-03-31 "
                "all had `oil_up:STRONG` AND broad capitulation; standard "
                "`brent_spike_e_and_p` UPGRADED E&P in those windows and was "
                "wrong direction (E&P -8.86% on 2026-03-17)."
            ),
            "trigger_signals": [
                "driver:oil_up:STRONG",
                "universe_5d_lt:-0.03",
                "breadth_lt:0.30"
            ],
            "min_triggers": 3,
            "historical_instances": [
                {
                    "date": "2026-03-17",
                    "context": "US-Iran tensions (event window) + IMF mission stress; Brent +12% / 21d; universe -5.93% / 5d; E&P -8.86% / 5d.",
                    "reactions": {"KSE100": {"d1": -0.018, "d5": -0.059, "d21": -0.164}},
                    "source": "PSX EOD; ICE Brent."
                }
            ],
            "playbook": (
                "DEFENSIVE posture: cash floor 60%, downgrade Cement / "
                "Banking / Power one notch (cyclicals get re-priced lower). "
                "Do NOT add E&P even though Brent is up — in this regime "
                "the wellhead-USD benefit is dominated by the systemic "
                "discount. Wait for breadth to recover above 40% before "
                "re-adding cyclical exposure. Hold cash 5-10 days; expect "
                "follow-on weakness for 2-3 weeks (21d return historically "
                "stays negative in this archetype)."
            ),
            "what_breaks_it": (
                "Brent reverses sharply or universe breadth recovers above "
                "40% — the regime flips back to a normal `brent_spike_e_and_p` "
                "set-up where E&P can be added on the wellhead-USD trade."
            ),
            "confidence": "MEDIUM",
            "research_basis": (
                "Per-sector mining 2026-05-13: 3 of the top-10 universe-wide "
                "DOWN moves in 2026 (Mar 9/17/31) had `oil_up:STRONG` PLUS "
                "broad sell-off. Pre-Round-2 playbook fired E&P upgrade in "
                "all 3 — 0% accuracy."
            ),
            "tags": ["oil", "risk-off", "systemic", "brent-spike",
                     "round-2-discovery"],
            "reactions": {
                "cash_floor_pct": 60,
                "sector_overlay": {
                    "Cement":  "downgrade_one",
                    "Banking": "downgrade_one",
                    "Power":   "downgrade_one"
                },
                "conviction_cap": "MEDIUM",
                "position_size_multiplier": 0.6,
                "narrative_note": (
                    "Oil-spike-as-systemic-risk-off — defensive 5-10d. "
                    "Cash 60%, cyclicals -1 notch. E&P NOT added (wellhead "
                    "benefit dominated by systemic discount in this regime)."
                )
            }
        }
        cases.append(new_case)
        log.append("R2: NEW case oil_spike_systemic_risk_off (catches Mar-2026 crash sequence)")

    # ---- R3: NEW oil_demand_destruction_risk_off -----------------------
    if "oil_demand_destruction_risk_off" not in by_id:
        new_case = {
            "id": "oil_demand_destruction_risk_off",
            "category": "macro_event",
            "title": "Oil DOWN paired with broad EM equity sell-off",
            "pattern": (
                "Brent down >=5% / 21d while PSX universe is also -2% / 5d "
                "and breadth is sub-30%. This is the global recession-fear "
                "regime — oil falls because global demand expectations are "
                "deteriorating; PSX falls along with EM equities as foreign "
                "flows turn risk-off. Discovered 2026-05-13: dates with "
                "`oil_down:MODERATE` AND broad sell-off had no playbook case. "
                "(2021-11-26 -4.89%, 2023-05-10 -3.52%, 2022-04-05 -3.15%.) "
                "Pre-Round-2, only `behavioural_panic_3day` would fire — "
                "but that's behavioural, not macro."
            ),
            "trigger_signals": [
                "driver:oil_down",
                "universe_5d_lt:-0.02",
                "breadth_lt:0.30"
            ],
            "min_triggers": 3,
            "historical_instances": [
                {
                    "date": "2021-11-26",
                    "context": "Omicron-variant scare; oil -10% / week, PSX -4.89% / 5d, breadth 8%.",
                    "reactions": {"KSE100": {"d1": -0.012, "d5": -0.049, "d21": -0.044}},
                    "source": "PSX EOD; ICE Brent."
                }
            ],
            "playbook": (
                "MILD DEFENSIVE: cash floor 50%, downgrade Banking + "
                "Cement one notch (cyclicals exposed to global growth). "
                "DO NOT downgrade E&P even though oil is falling — "
                "Pakistan E&P has hedge contracts that smooth wellhead "
                "revenue across short oil moves. Hold 5 days. Re-evaluate "
                "if breadth recovers; the rebound from these is usually "
                "sharp (1-2 weeks once foreign flows stabilise)."
            ),
            "what_breaks_it": (
                "Oil reverses (supply shock) or breadth recovers above "
                "40% — flip back to neutral cyclical posture."
            ),
            "confidence": "MEDIUM",
            "research_basis": (
                "Per-sector mining 2026-05-13. 4-6 dates over 2021-2025 "
                "with `oil_down:MODERATE` AND universe -2% / breadth low "
                "had no playbook case fire. Aggregate forward 5d "
                "(2026-05-13 catalog) was -1.8% on average."
            ),
            "tags": ["oil", "demand-destruction", "risk-off",
                     "round-2-discovery"],
            "reactions": {
                "cash_floor_pct": 50,
                "sector_overlay": {
                    "Banking": "downgrade_one",
                    "Cement":  "downgrade_one"
                },
                "position_size_multiplier": 0.7,
                "narrative_note": (
                    "Oil-down + broad sell-off (global demand-destruction "
                    "regime) — defensive 5d; cash 50%, cyclicals -1 notch."
                )
            }
        }
        cases.append(new_case)
        log.append("R3: NEW case oil_demand_destruction_risk_off (catches oil-down + broad sell-off)")

    # ---- R4: imf_sba_eff_approval — ADD Power downgrade ---------------
    c = by_id.get("imf_sba_eff_approval")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                "Oil & Gas E&P": "upgrade_one",   # backtest: 71% accuracy / +0.43%
                "Cement":        "upgrade_one",   # 2024-10-25 +16.84%
                "OMC":           "upgrade_one",   # 2024-10-16 +17.96%
                # Power downgrade ADDED 2026-05-13 R4: 2024-09-27 -11.62%,
                # 2024-10-16 -14.27% — IMF programs typically demand power
                # tariff hikes that hurt collection.
                "Power":         "downgrade_one"
            },
            "symbol_overlay": {
                "OGDC": {"min_bucket": "ADD"},
                "PPL":  {"min_bucket": "ADD"},
                "PSO":  {"min_bucket": "HOLD"},
                "ATRL": {"min_bucket": "HOLD"},
                # Power names clamped down
                "HUBC":  {"max_bucket": "HOLD"},
                "KAPCO": {"max_bucket": "HOLD"},
                "NPL":   {"max_bucket": "HOLD"}
            },
            "cash_floor_pct": 30,
            "narrative_note": (
                "IMF program approved — E&P / Cement / OMC relief lift; "
                "Power -1 notch (tariff-hike pressure on collections, "
                "verified 2024-09/10 -11% to -14% Power moves)."
            ),
        }
        log.append("R4: imf_sba_eff_approval ADDED Power downgrade (catches Sep/Oct 2024 -14% moves)")

    # ---- R5: pkr_devaluation_shock — tighten + re-add Cement ----------
    c = by_id.get("pkr_devaluation_shock")
    if c:
        c["trigger_signals"] = ["driver:pkr_weak:STRONG"]  # was driver:pkr_weak (any mag)
        c["reactions"] = {
            "sector_overlay": {
                "Oil & Gas E&P": "upgrade_one",
                "Cement":        "downgrade_one",  # RE-ADDED — STRONG only
                "Autos":         "downgrade_one"   # 59% accuracy retained
            },
            "symbol_overlay": {
                "OGDC": {"min_bucket": "ADD"},
                "PPL":  {"min_bucket": "ADD"},
                "MARI": {"min_bucket": "ADD"}
            },
            "narrative_note": (
                "PKR STRONG devaluation — E&P wellhead-USD tailwind; "
                "Cement crushed on imported coal/fuel costs (re-added "
                "2026-05-13 R5: dropping the MODERATE-magnitude false "
                "positives lifts the overlay accuracy)."
            ),
        }
        log.append("R5: pkr_devaluation_shock tightened to STRONG; Cement downgrade RE-ADDED")

    # ---- R6: brent_spike_e_and_p — add breadth + non-crisis guards -----
    c = by_id.get("brent_spike_e_and_p")
    if c:
        c["trigger_signals"] = [
            "driver:oil_up",
            "breadth_gt:0.40",
            "universe_5d_gt:-0.03"
        ]
        c["min_triggers"] = 3
        c["reactions"] = {
            "sector_overlay": {
                "Oil & Gas E&P": "upgrade_one"
            },
            "symbol_overlay": {
                "OGDC": {"min_bucket": "ADD"},
                "PPL":  {"min_bucket": "ADD"},
                "MARI": {"min_bucket": "ADD"}
            },
            "narrative_note": (
                "Brent up AND market healthy (breadth >40% AND universe "
                "5d > -3%) — clean E&P upgrade trade. (Round-2 added "
                "the breadth/regime guards: prior case fired E&P upgrade "
                "in oil-spike-as-risk-off regimes and was wrong-direction "
                "—49% accuracy.)"
            ),
        }
        log.append("R6: brent_spike_e_and_p added breadth+regime guards "
                    "(filters out risk-off oil spikes)")

    # ---- R7: us_iran_oil_spike — drop E&P upgrade --------------------
    c = by_id.get("us_iran_oil_spike")
    if c:
        c["reactions"] = {
            # E&P sector upgrade DROPPED — the new oil_spike_systemic_risk_off
            # case handles the defensive side for true risk-off, and
            # brent_spike_e_and_p (now guarded) handles clean E&P trades.
            # us_iran_oil_spike now just adjusts SPECIFIC E&P symbols
            # without lifting the whole sector bucket.
            "symbol_overlay": {
                "OGDC": {"min_bucket": "HOLD", "weight_floor_pct": 4.0},
                "PPL":  {"min_bucket": "HOLD", "weight_floor_pct": 3.0},
                "MARI": {"min_bucket": "HOLD", "weight_floor_pct": 2.5},
                "PSO":  {"max_bucket": "HOLD"},
                "ATRL": {"max_bucket": "HOLD"},
                "APL":  {"max_bucket": "HOLD"}
            },
            "cash_floor_pct": 40,
            "narrative_note": (
                "US-Iran oil spike — protect E&P core positions but no "
                "sector-wide ADD (oil-spike-as-risk-off regime would "
                "neutralise the trade). OMC/Refining ceiling at HOLD."
            ),
        }
        log.append("R7: us_iran_oil_spike dropped E&P sector upgrade "
                    "(the systemic risk-off case now handles defensive)")

    # ---- Persist ------------------------------------------------------
    CASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[fixes-v2] applied {len(log)} changes:")
    for line in log:
        print(f"  {line}")
    print(f"[fixes-v2] total cases now: {len(cases)} (3 new added)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
