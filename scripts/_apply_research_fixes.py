"""Apply research-driven fixes to the playbook based on the 5-year backtest.

Backtest summary (data/_research/backtest_summary.json):
  - 258 weekly samples 2021-06 -> 2026-05
  - Σ baseline 5d  = +52.47%
  - Σ overlay  5d  = +8.34%   (-44.12% edge — overlay was TOO defensive)
  - Σ baseline 21d = +231.21%
  - Σ overlay  21d = +32.44%  (-198.77% edge)

Root causes (per data/_research/backtest_report.md):

1. **`imf_review_mission_week` fires 83% of weeks** because `min_triggers:1`
   plus `regime:NORMAL` (true ~80% of the time) fires it without an actual
   IMF event. Each fire raises cash floor to 85% and downgrades 4 sectors.
   This single bug accounts for most of the alpha destruction.

2. **`risk_off_universe_session_pause` fires after recovery has begun**
   (17% of weeks; 34% 5d hit rate). Fires on any -2% 5d move which is too
   loose for PSX volatility.

3. **`pkr_devaluation_shock × Cement downgrade`** is wrong direction (41%
   accuracy on 22 fires). Cement actually outperformed during PKR shocks.

4. **`imf_sba_eff_approval × Banking upgrade`** is wrong (29% accuracy).
   Banks don't rally on IMF approval; only E&P does (71% accuracy).

5. **`imf_review_completed × E&P upgrade`** is wrong (0% accuracy / 8 fires).

6. **`banking_nim_regime_low × Banking downgrade`** is wrong (36% accuracy);
   banks actually outperformed +0.62% vs universe in low-rate periods.

7. **`volume_confirmation_breakout` haircut hurts** (case has +0.32% edge but
   the 0.5x position multiplier neutralises it).

Fixes applied here:
  F1: imf_review_mission_week     min_triggers 1 -> 2 (require event AND regime)
  F2: imf_review_mission_week     reactions soften (cash 85->60, drop 2 sectors)
  F3: risk_off_universe_session_pause  trigger -0.02 -> -0.04 + breadth_lt:0.40
                                       AND min_triggers 1 -> 2
  F4: pkr_devaluation_shock        reactions: drop Cement downgrade
  F5: imf_sba_eff_approval         reactions: drop Banking from sector_overlay
  F6: imf_review_completed         reactions: drop Oil & Gas E&P (keep Banking)
  F7: banking_nim_regime_low       reactions: drop sector_overlay
  F8: volume_confirmation_breakout reactions: drop position_size_multiplier
  F9: pre_imf_de_risk_window       reactions soften (cash 70->50)
  F10: brent_spike_cement_margin_squeeze   trigger 100 -> 105 (USD)
  F11: us_iran_oil_spike           reactions soften (drop OMC downgrade —
                                   accuracy 50% on 24 fires, no edge)
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
    cases = data["cases"]
    by_id = {c.get("id"): c for c in cases if isinstance(c, dict)}

    changes_log: list[str] = []

    # --- F1 + F2: imf_review_mission_week --------------------------------
    c = by_id.get("imf_review_mission_week")
    if c:
        c["min_triggers"] = 2  # require event AND regime
        c["trigger_signals"] = [
            "event:imf_mission_active",
            "regime:NORMAL",
        ]
        c["reactions"] = {
            "cash_floor_pct": 60,            # was 85
            "sector_overlay": {
                "Banking": "downgrade_one",  # was 4 sectors; keep just 2
                "Power":   "downgrade_one",
            },
            "symbol_overlay": {
                "MCB":   {"min_bucket": "HOLD"},
                "UBL":   {"min_bucket": "HOLD"},
                "FFC":   {"min_bucket": "HOLD"},
                "OGDC":  {"min_bucket": "HOLD"},
                "KAPCO": {"max_bucket": "AVOID"},
                "EPCL":  {"max_bucket": "AVOID"},
            },
            "conviction_cap": "MEDIUM",
            "position_size_multiplier": 0.7,  # was 0.5 (less severe)
            "narrative_note": (
                "IMF mission active — moderate defensive. Cash 60%, "
                "Banks/Power -1 notch, half-size leveraged small-caps. "
                "(Calibrated 2026-05-13 backtest: prior settings -1.7% per "
                "fire on universe drift +0.4%.)"
            ),
        }
        changes_log.append("F1+F2: imf_review_mission_week min_triggers 2, "
                            "cash 85->60, 4 sectors -> 2, position_size 0.5->0.7")

    # --- F3: risk_off_universe_session_pause -----------------------------
    c = by_id.get("risk_off_universe_session_pause")
    if c:
        c["trigger_signals"] = [
            "universe_5d_lt:-0.04",   # was -0.02
            "breadth_lt:0.40",
        ]
        c["min_triggers"] = 2          # was 1
        c["reactions"] = {
            "cash_floor_pct": 60,      # was 70
            "position_size_multiplier": 0.7,  # was 0.5
            "sector_overlay": {
                "Power":  "downgrade_one",  # keep Power only
            },
            "narrative_note": (
                "Universe broad sell-off (-4% 5d AND breadth <40%) — "
                "moderate defensive: cash 60%, Power -1 notch."
            ),
        }
        changes_log.append("F3: risk_off_universe_session_pause -2% -> -4% AND "
                            "breadth<40%, min_triggers 1 -> 2, soften overlay")

    # --- F4: pkr_devaluation_shock --------------------------------------
    c = by_id.get("pkr_devaluation_shock")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                "Oil & Gas E&P": "upgrade_one",  # works (rare wins)
                # "Cement": dropped — backtest 41% accuracy (false signal)
                "Autos": "downgrade_one",         # 59% accuracy
            },
            "symbol_overlay": {
                "OGDC": {"min_bucket": "ADD"},
                "PPL":  {"min_bucket": "ADD"},
                "MARI": {"min_bucket": "ADD"},
            },
            "narrative_note": (
                "PKR devaluation — E&P wellhead-USD tailwind; Autos "
                "pressured by imports. Cement dropped from overlay (backtest "
                "showed 41% directional accuracy)."
            ),
        }
        changes_log.append("F4: pkr_devaluation_shock dropped Cement downgrade "
                            "(was 41% accuracy)")

    # --- F5: imf_sba_eff_approval ---------------------------------------
    c = by_id.get("imf_sba_eff_approval")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                # "Banking": dropped — 29% accuracy on backtest
                "Oil & Gas E&P": "upgrade_one",  # 71% accuracy
            },
            "symbol_overlay": {
                # Drop the bank min_bucket clamps too
                "OGDC": {"min_bucket": "ADD"},
                "PPL":  {"min_bucket": "ADD"},
            },
            "cash_floor_pct": 30,
            "narrative_note": (
                "IMF program approved — E&P relief rally trade only. "
                "Banking removed from overlay (backtest 29% accuracy)."
            ),
        }
        changes_log.append("F5: imf_sba_eff_approval dropped Banking upgrade "
                            "(was 29% accuracy)")

    # --- F6: imf_review_completed ---------------------------------------
    c = by_id.get("imf_review_completed")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                "Banking": "upgrade_one",  # 100% accuracy on 14 fires (n small but consistent)
                # "Oil & Gas E&P": dropped — 0% accuracy on 8 fires
            },
            "cash_floor_pct": 30,
            "narrative_note": (
                "IMF review completed — Banking-only relief lift. E&P "
                "dropped (backtest 0% accuracy)."
            ),
        }
        changes_log.append("F6: imf_review_completed dropped E&P upgrade "
                            "(was 0% accuracy)")

    # --- F7: banking_nim_regime_low -------------------------------------
    c = by_id.get("banking_nim_regime_low")
    if c:
        c["reactions"] = {
            # sector_overlay dropped — backtest showed banks outperformed
            # +0.62% vs univ in low-rate periods (36% accuracy on downgrade)
            "narrative_note": (
                "Policy rate bottom-quartile — Bank NIM headwind narrative. "
                "Auto-downgrade REMOVED 2026-05-13 (backtest: banks actually "
                "outperformed +0.62% vs universe in 196 low-rate sessions; "
                "rule was wrong direction)."
            ),
        }
        changes_log.append("F7: banking_nim_regime_low dropped Banking downgrade "
                            "(was 36% accuracy — banks outperformed)")

    # --- F8: volume_confirmation_breakout -------------------------------
    c = by_id.get("volume_confirmation_breakout")
    if c:
        c["reactions"] = {
            # position_size_multiplier 0.5 dropped — backtest +0.32% edge
            # was being neutralised by the haircut. Just keep the narrative.
            "narrative_note": (
                "Broad volume-confirmed breakouts — 5d tactical signal. "
                "Position-size haircut REMOVED 2026-05-13 (backtest +0.32% "
                "edge per fire; haircut was neutralising the alpha)."
            ),
        }
        changes_log.append("F8: volume_confirmation_breakout dropped 0.5x size "
                            "haircut (was killing +0.32% edge)")

    # --- F9: pre_imf_de_risk_window -------------------------------------
    c = by_id.get("pre_imf_de_risk_window")
    if c:
        c["reactions"] = {
            "cash_floor_pct": 50,   # was 70
            "sector_overlay": {
                "Banking": "downgrade_one",
                # Cement dropped — same reasoning as imf_review_mission_week
            },
            "conviction_cap": "MEDIUM",
            "narrative_note": (
                "IMF mission active (any regime) — moderate defensive. "
                "Cash 50%, Banks -1 notch."
            ),
        }
        changes_log.append("F9: pre_imf_de_risk_window cash 70->50, drop Cement")

    # --- F10: brent_spike_cement_margin_squeeze -------------------------
    c = by_id.get("brent_spike_cement_margin_squeeze")
    if c:
        # Tighten the threshold from $100 to $105. The 30 fires at $100+
        # with 54% accuracy / -0.56% edge suggest it's firing on too many
        # marginal Brent levels. Higher threshold = fewer but better fires.
        c["trigger_signals"] = ["brent_gte:105"]
        changes_log.append("F10: brent_spike_cement_margin_squeeze "
                            "threshold $100 -> $105")

    # --- F11: us_iran_oil_spike -----------------------------------------
    c = by_id.get("us_iran_oil_spike")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                "Oil & Gas E&P": "upgrade_one",   # keep — rare event
                # "OMC": dropped — 50% accuracy on 24 fires, no edge
                # "Refining": dropped same reasoning
            },
            "symbol_overlay": {
                "OGDC": {"min_bucket": "ADD", "weight_floor_pct": 5.0},
                "PPL":  {"min_bucket": "ADD", "weight_floor_pct": 4.0},
                "MARI": {"min_bucket": "ADD", "weight_floor_pct": 3.0},
                "PSO":  {"max_bucket": "HOLD"},
                "ATRL": {"max_bucket": "HOLD"},
                "APL":  {"max_bucket": "HOLD"},
            },
            "narrative_note": (
                "US-Iran oil spike — ADD E&P only (sector overlay). "
                "OMC/Refining sector downgrade REMOVED (backtest 50% accuracy "
                "on 24 fires); per-symbol HOLD ceiling kept on PSO/ATRL/APL."
            ),
        }
        changes_log.append("F11: us_iran_oil_spike dropped OMC + Refining "
                            "sector downgrade (was 50% accuracy)")

    # ---- Persist --------------------------------------------------------
    CASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[fixes] applied {len(changes_log)} changes to {len(cases)} cases:")
    for c in changes_log:
        print(f"  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
