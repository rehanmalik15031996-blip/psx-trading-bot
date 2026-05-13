"""ROUND-3 cleanup based on Round-2 backtest evidence.

Round-2 added 3 new cases + tuned 4 existing. Re-running the backtest
showed mixed results from `nth_rate_cut_immediate_window`:

  * Banking upgrade        →  63 fires, 78% accuracy, +2.08% sec-vs-univ ✓
  * Cement  upgrade        →  45 fires, **11% accuracy**, -1.45%  ✗
  * Power   upgrade        →  36 fires, 44% accuracy, -0.88%  ✗
  * Conglom upgrade        →  27 fires, 44% accuracy, -0.06%  ~
  * OMC     upgrade        →  27 fires, 56% accuracy, -0.20%  ~

The case correctly identifies the rate-cut window, but the BROAD-rally
hypothesis only generalises for Banking. The 2025-05-15 +16% mega-rally
was a single outlier where every sector ran together; in the other 8
fires the non-Banking sectors disagreed with the Banking signal.

Pkr_devaluation_shock × Cement (re-added in R5) still at 38% accuracy
on 40 sector-events even after tightening to STRONG-only — drop it.

Round-3 changes:
  R8: nth_rate_cut_immediate_window  keep ONLY Banking upgrade;
                                     drop Cement / Power / Conglomerate / OMC
  R9: pkr_devaluation_shock          drop Cement again (38% acc even with STRONG)
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
    by_id = {c.get("id"): c for c in data["cases"] if isinstance(c, dict)}
    log: list[str] = []

    # ---- R8: nth_rate_cut_immediate_window — Banking-only upgrade ----
    c = by_id.get("nth_rate_cut_immediate_window")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                "Banking": "upgrade_one"   # only one with backtested edge
            },
            "symbol_overlay": {
                # keep some defensive symbol clamps for the leveraged names
                # in case they DO run, but don't sector-wide upgrade them
                "MLCF":  {"min_bucket": "HOLD"},
                "FCCL":  {"min_bucket": "HOLD"},
                "EPCL":  {"min_bucket": "HOLD"},
                "ENGROH": {"min_bucket": "HOLD"},
                "MCB":   {"min_bucket": "ADD"},
                "UBL":   {"min_bucket": "ADD"},
                "MEBL":  {"min_bucket": "ADD"}
            },
            "cash_floor_pct": 25,
            "narrative_note": (
                "Nth-rate-cut immediate window (days 1-14) — Banking-only "
                "sector upgrade (78% accuracy in 5y backtest, +2.08% "
                "vs-univ); leveraged sectors held at HOLD min (the "
                "broad-rally hypothesis only generalised to Banks)."
            )
        }
        log.append("R8: nth_rate_cut_immediate_window kept Banking only "
                    "(other sectors were 11-44% accuracy)")

    # ---- R9: pkr_devaluation_shock — drop Cement again ---------------
    c = by_id.get("pkr_devaluation_shock")
    if c:
        c["reactions"] = {
            "sector_overlay": {
                "Oil & Gas E&P": "upgrade_one",
                "Autos":         "downgrade_one"
                # Cement dropped AGAIN — even after tightening trigger to
                # pkr_weak:STRONG only, the sector-overlay accuracy was
                # 38% on 40 stock-events. Cement's response to PKR shocks
                # is too non-stationary to encode as an unconditional
                # downgrade.
            },
            "symbol_overlay": {
                "OGDC": {"min_bucket": "ADD"},
                "PPL":  {"min_bucket": "ADD"},
                "MARI": {"min_bucket": "ADD"}
            },
            "narrative_note": (
                "PKR STRONG devaluation — E&P wellhead-USD tailwind; "
                "Autos pressured by imports. Cement downgrade DROPPED "
                "(again, after STRONG-only filter still scored 38% — "
                "Cement's PKR response is non-stationary)."
            )
        }
        log.append("R9: pkr_devaluation_shock dropped Cement (still 38% acc with STRONG)")

    # ---- Persist -----------------------------------------------------
    CASES_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"[fixes-v3] applied {len(log)} cleanup changes:")
    for line in log:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
