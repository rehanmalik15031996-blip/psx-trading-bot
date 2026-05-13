"""Inject `reactions` block into every case in data/playbook/cases.json.

The `reactions` schema (designed 2026-05-13 after the May11-13 sell-off where
the IMF case fired but no actions were taken):

  reactions: {
    cash_floor_pct: int  | null,        # min cash % across portfolio
    sector_overlay:   { sector: action },  # downgrade_one | upgrade_one
    symbol_overlay:   { sym: { min_bucket, max_bucket, weight_floor_pct } },
    conviction_cap:   "LOW" | "MEDIUM" | "HIGH" | null,
    position_size_multiplier: float | null,  # haircut on new BUY weights
    narrative_note:   str
  }

Bucket ordering (low-conviction -> high-conviction):
  AVOID < TRIM < WATCH < HOLD < ADD < BUY

`downgrade_one`: every per-symbol bucket in the sector shifts down ONE notch.
`upgrade_one`:   shifts up ONE notch.

The `brain/strategist_overlays.py` engine reads fired playbook cases'
`reactions` and applies them deterministically AFTER the LLM (or fallback)
produces its actions list. Idempotent: case-merge order = match_score desc.
"""
from __future__ import annotations
import json, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

CASES_PATH = ROOT / "data/playbook/cases.json"

# Per-case reaction recipes. Sector names match strategist `sector` field
# (see action.sector in actions[]) — verified against 2026-05-13 strategist.
REACTIONS: dict[str, dict] = {
    # ---------- macro / policy ----------
    "circular_debt_resolution_large": {
        "sector_overlay": {
            "Power": "upgrade_one",
            "OMC": "upgrade_one",
            "Oil & Gas E&P": "upgrade_one",
            "Banking": "upgrade_one",
        },
        "narrative_note": "Circular-debt clearance unlocks Power/OMC/E&P/Banks — bias up.",
    },
    "circular_debt_worsening_large": {
        "sector_overlay": {"Power": "downgrade_one"},
        "symbol_overlay": {
            "HUBC": {"max_bucket": "HOLD"},
            "KEL":  {"max_bucket": "HOLD"},
            "PSO":  {"max_bucket": "HOLD"},
        },
        "narrative_note": "Power-sector circular debt worsening — TRIM IPPs and PSO.",
    },
    "sbp_rate_cut_cycle_initiation": {
        "sector_overlay": {
            "Cement": "upgrade_one",
            "Conglomerate": "upgrade_one",
            "Autos": "upgrade_one",
            "Banking": "downgrade_one",
        },
        "narrative_note": "First rate cut of cycle — Cement / Conglomerate / Auto leg up; Banks NIM compresses.",
    },
    "sbp_rate_hike_shock": {
        "cash_floor_pct": 70,
        "sector_overlay": {
            "Cement": "downgrade_one",
            "Autos": "downgrade_one",
            "Conglomerate": "downgrade_one",
        },
        "position_size_multiplier": 0.7,
        "narrative_note": "SBP rate-hike shock — defensive 5-10d. TRIM Cement/Auto/Conglomerate.",
    },
    "rate_cycle_pivot_diagnostic": {
        "narrative_note": "Rate-cycle pivot detected — surface explicitly in narrative; no auto-action.",
    },
    "post_cut_cycle_continuation": {
        "sector_overlay": {"Cement": "upgrade_one", "Conglomerate": "upgrade_one"},
        "symbol_overlay": {
            "PABC": {"min_bucket": "WATCH"},
            "EPCL": {"min_bucket": "WATCH"},
            "KOHC": {"min_bucket": "WATCH"},
            "NPL":  {"min_bucket": "WATCH"},
        },
        "narrative_note": "Post-cut continuation — small/mid value catches up.",
    },
    "imf_sba_eff_approval": {
        "sector_overlay": {
            "Banking": "upgrade_one",
            "Oil & Gas E&P": "upgrade_one",
        },
        "symbol_overlay": {
            "HBL": {"min_bucket": "ADD"},
            "UBL": {"min_bucket": "ADD"},
            "MCB": {"min_bucket": "ADD"},
        },
        "cash_floor_pct": 30,
        "narrative_note": "IMF program approved — relief rally trade. ADD large-cap Banks + E&P 5d window.",
    },
    "imf_review_completed": {
        "sector_overlay": {
            "Banking": "upgrade_one",
            "Oil & Gas E&P": "upgrade_one",
        },
        "cash_floor_pct": 30,
        "narrative_note": "IMF review completed — 5d tactical window. ADD Banks + E&P.",
    },
    "pkr_devaluation_shock": {
        "sector_overlay": {
            "Oil & Gas E&P": "upgrade_one",
            "Cement": "downgrade_one",
            "Autos": "downgrade_one",
        },
        "symbol_overlay": {
            "OGDC": {"min_bucket": "ADD"},
            "PPL":  {"min_bucket": "ADD"},
            "MARI": {"min_bucket": "ADD"},
        },
        "narrative_note": "PKR devaluation shock — E&P wellhead-USD tailwind; Cement/Auto pressured by imports.",
    },

    # ---------- flow / behavioural ----------
    "fipi_capitulation": {
        "sector_overlay": {"Banking": "upgrade_one"},
        "narrative_note": "FIPI capitulation — contrarian setup IF macro is benign (check NORMAL regime).",
    },
    "behavioural_panic_3day": {
        "symbol_overlay": {
            "HBL":  {"min_bucket": "WATCH"},
            "MCB":  {"min_bucket": "WATCH"},
            "OGDC": {"min_bucket": "WATCH"},
            "FFC":  {"min_bucket": "WATCH"},
        },
        "narrative_note": "3-day panic — tactical BUY in blue-chips IF macro benign.",
    },
    "phase1_cash_in_uptrend": {
        "cash_floor_pct": 70,
        "narrative_note": "Phase-1 CASH while breadth/FIPI improving — keep dry powder, watch for confirmation.",
    },
    "earnings_blackout_concentration": {
        "conviction_cap": "MEDIUM",
        "narrative_note": "Multiple universe names in 5d earnings blackout — HOLD-only, downgrade conviction.",
    },

    # ---------- sector-specific ----------
    "cement_coal_shock": {
        "sector_overlay": {"Cement": "downgrade_one"},
        "symbol_overlay": {
            "DGKC": {"max_bucket": "WATCH"},
            "MLCF": {"max_bucket": "WATCH"},
        },
        "narrative_note": "Imported coal shock — TRIM north-zone Cement (DGKC, MLCF).",
    },
    "brent_spike_e_and_p": {
        "sector_overlay": {"Oil & Gas E&P": "upgrade_one"},
        "symbol_overlay": {
            "OGDC": {"min_bucket": "ADD"},
            "PPL":  {"min_bucket": "ADD"},
            "MARI": {"min_bucket": "ADD"},
        },
        "narrative_note": "Brent +10% in 21d — ADD E&P (wellhead-USD tailwind).",
    },
    "election_window_chop": {
        "cash_floor_pct": 70,
        "position_size_multiplier": 0.5,
        "conviction_cap": "MEDIUM",
        "narrative_note": "Election window — half-size all new entries; AVOID illiquid mid-caps.",
    },

    # ---------- mutual-fund flow ----------
    "mf_accumulation_strong": {
        "narrative_note": "MF accumulation broad streak — names auto-listed as institutional-buy candidates.",
    },
    "mf_distribution_strong": {
        "conviction_cap": "MEDIUM",
        "narrative_note": "MF distribution streak — TRIM/HOLD-only on flagged names.",
    },
    "mf_initiation_cluster": {
        "narrative_note": "3+ funds initiated — BUY full size on flagged names within 5 sessions.",
    },
    "mf_capitulation_with_value": {
        "narrative_note": "MF distribution + BUY_VALUE overlap — contrarian 1/3-position increments.",
    },
    "mf_smart_money_divergence": {
        "narrative_note": "MF accumulation while price flat — institutional setup, BUY/ADD divergent names.",
    },

    # ---------- banking NIM regime ----------
    "banking_nim_regime_high": {
        "sector_overlay": {"Banking": "upgrade_one"},
        "symbol_overlay": {
            "HBL": {"min_bucket": "ADD"},
            "UBL": {"min_bucket": "ADD"},
            "MCB": {"min_bucket": "ADD"},
        },
        "narrative_note": "Policy rate top-quartile — large-cap Bank NIM tailwind, BIAS LONG 60-90d.",
    },
    "banking_nim_regime_low": {
        "sector_overlay": {"Banking": "downgrade_one"},
        "narrative_note": "Policy rate bottom-quartile — Bank NIM headwind, BIAS NEUTRAL/UNDERWEIGHT.",
    },

    # ---------- broad regime modifiers ----------
    "mf_universe_distribution_broad": {
        "cash_floor_pct": 70,
        "position_size_multiplier": 0.85,
        "narrative_note": "Broad MF distribution across top-30 universe — reduce gross exposure 10-20%.",
    },
    "volume_confirmation_breakout": {
        "position_size_multiplier": 0.5,
        "narrative_note": "Broad volume-confirmed breakouts — half-normal sizing on flagged names; 5d tactical.",
    },

    # ---------- the TWO that mattered May 13 ----------
    "us_iran_oil_spike": {
        "sector_overlay": {
            "Oil & Gas E&P": "upgrade_one",
            "OMC": "downgrade_one",
            "Refining": "downgrade_one",
        },
        "symbol_overlay": {
            "OGDC": {"min_bucket": "ADD", "weight_floor_pct": 5.0},
            "PPL":  {"min_bucket": "ADD", "weight_floor_pct": 4.0},
            "MARI": {"min_bucket": "ADD", "weight_floor_pct": 3.0},
            "PSO":  {"max_bucket": "HOLD"},
            "ATRL": {"max_bucket": "HOLD"},
            "APL":  {"max_bucket": "HOLD"},
        },
        "narrative_note": "US-Iran oil spike — ADD E&P (Brent floor); TRIM/AVOID OMC/Refining (margin squeeze).",
    },
    "imf_review_mission_week": {
        "cash_floor_pct": 85,
        "sector_overlay": {
            "Banking": "downgrade_one",
            "Cement": "downgrade_one",
            "Power": "downgrade_one",
            "Conglomerate": "downgrade_one",
        },
        "symbol_overlay": {
            "MCB":  {"min_bucket": "HOLD"},
            "UBL":  {"min_bucket": "HOLD"},
            "FFC":  {"min_bucket": "HOLD"},
            "OGDC": {"min_bucket": "HOLD"},
            "KAPCO": {"max_bucket": "AVOID"},
            "EPCL": {"max_bucket": "AVOID"},
            "PABC": {"max_bucket": "AVOID"},
            "COLG": {"max_bucket": "AVOID"},
        },
        "conviction_cap": "MEDIUM",
        "position_size_multiplier": 0.5,
        "narrative_note": (
            "IMF review mission week — defensive. Auto-trim Banks/Cement/Power one notch. "
            "Cash floor 85%. AVOID highly-leveraged + small caps (KAPCO, EPCL, PABC, COLG)."
        ),
    },
    "narrow_breadth_low_turnover_pause": {
        "cash_floor_pct": 70,
        "position_size_multiplier": 0.5,
        "narrative_note": "Wait-and-see regime — DON'T chase day's gainers; hold quality.",
    },
}


def main() -> int:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    print(f"[reactions] {len(cases)} cases in file")
    print(f"[reactions] {len(REACTIONS)} reaction recipes designed")

    # Update _schema doc to declare the new field
    schema = data.get("_schema", {})
    fields = schema.setdefault("fields", {})
    fields["reactions"] = (
        "OPTIONAL deterministic action recipe applied by brain/strategist_overlays.py "
        "AFTER the LLM (or fallback) produces actions. Schema: "
        "{cash_floor_pct, sector_overlay:{sector:downgrade_one|upgrade_one}, "
        "symbol_overlay:{sym:{min_bucket,max_bucket,weight_floor_pct}}, "
        "conviction_cap, position_size_multiplier, narrative_note}. "
        "Bucket order: AVOID<TRIM<WATCH<HOLD<ADD<BUY."
    )
    data["_schema"] = schema

    missing = []
    updated = 0
    for c in cases:
        cid = c.get("id")
        if cid in REACTIONS:
            c["reactions"] = REACTIONS[cid]
            updated += 1
        else:
            missing.append(cid)

    if missing:
        print(f"[reactions] WARNING: no recipe for: {missing}")

    extra = set(REACTIONS) - {c.get("id") for c in cases}
    if extra:
        print(f"[reactions] WARNING: recipes with no matching case: {sorted(extra)}")

    print(f"[reactions] updated {updated}/{len(cases)} cases")

    out_text = json.dumps(data, indent=2, ensure_ascii=False)
    CASES_PATH.write_text(out_text, encoding="utf-8")
    print(f"[reactions] wrote {CASES_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
