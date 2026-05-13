"""Analyse the per-date backtest dump and produce three rollup artifacts:

  1. data/_research/backtest_per_case.json
     Per-case: fire_count, hit_rate (5d + 21d), avg_pnl_when_fired,
     and avg_pnl_NOT_fired so you can see the directional EDGE the
     case actually delivered (HIT pct alone is misleading because
     the universe drifts up over the long run).

  2. data/_research/backtest_per_sector_overlay.json
     For each (case_id, sector, action) tuple in the reactions
     dictionary: how many times did the action fire, and when it did,
     what was the SECTOR's forward return vs the universe's? A
     `Banking → downgrade_one` is "correct" if Banks underperformed
     the universe in fwd 5d.

  3. data/_research/backtest_summary.json
     Aggregate portfolio metrics: total return baseline vs overlay,
     hit rate, drawdown, n_fires_per_week histogram, gap weeks
     (no fire on big moves).

  4. data/_research/backtest_report.md
     Human-readable summary for the ranking + tuning loop.
"""
from __future__ import annotations
import json
import sys
import statistics
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = ROOT / "data" / "_research"
PER_DATE_PATH = OUT_DIR / "backtest_per_date.json"

# Hand-coded case direction (from historical_test_playbook.py + extended)
CASE_EXPECTED_DIRECTION: dict[str, str] = {
    "circular_debt_resolution_large":  "UP",
    "sbp_rate_cut_cycle_initiation":   "UP",
    "imf_sba_eff_approval":            "UP",
    "imf_review_completed":            "UP",
    "post_cut_cycle_continuation":     "UP",
    "phase1_cash_in_uptrend":          "UP",
    "behavioural_panic_3day":          "UP",
    "fipi_capitulation":               "UP",
    "brent_spike_e_and_p":             "UP",
    "circular_debt_worsening_large":   "DOWN",
    "sbp_rate_hike_shock":             "DOWN",
    "nth_rate_cut_profit_taking":      "DOWN",
    "pkr_devaluation_shock":           "MIXED",
    "cement_coal_shock":               "DOWN",
    "election_window_chop":            "FLAT",
    "earnings_blackout_concentration": "FLAT",
    "mf_accumulation_strong":          "UP",
    "mf_distribution_strong":          "DOWN",
    "mf_initiation_cluster":           "UP",
    "mf_capitulation_with_value":      "UP",
    "mf_smart_money_divergence":       "UP",
    "mf_universe_distribution_broad":  "DOWN",
    "volume_confirmation_breakout":    "UP",
    "banking_nim_regime_high":         "UP",
    "banking_nim_regime_low":          "DOWN",
    "rate_cycle_pivot_diagnostic":     "FLAT",
    # New 2026-05-13 cases
    "us_iran_oil_spike":               "MIXED",
    "imf_review_mission_week":         "FLAT",
    "narrow_breadth_low_turnover_pause": "FLAT",
    "risk_off_universe_session_pause": "DOWN",
    "brent_spike_cement_margin_squeeze": "DOWN",
    "pre_imf_de_risk_window":          "FLAT",
}


def _classify_pnl(case_id: str, ret_pct: float) -> str:
    expected = CASE_EXPECTED_DIRECTION.get(case_id, "?")
    if expected == "UP":
        return "HIT" if ret_pct > 0.5 else "MISS"
    if expected == "DOWN":
        return "HIT" if ret_pct < -0.5 else "MISS"
    if expected == "FLAT":
        return "HIT" if abs(ret_pct) < 4.0 else "MISS"
    if expected == "MIXED":
        return "HIT" if abs(ret_pct) > 2.0 else "MISS"
    return "?"


def main() -> int:
    if not PER_DATE_PATH.exists():
        print(f"[analyse] missing {PER_DATE_PATH} — run _research_backtest.py first")
        return 1
    rows = json.loads(PER_DATE_PATH.read_text(encoding="utf-8"))
    if not rows:
        print("[analyse] empty backtest data")
        return 1

    print(f"[analyse] {len(rows)} backtest dates")

    # ---- 1. Per-case metrics --------------------------------------------
    case_fires: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for f in row.get("fired") or []:
            case_fires[f["id"]].append({
                "as_of":      row["as_of"],
                "score":      f.get("score"),
                "univ_5d":    row.get("univ_ret_5d_pct"),
                "univ_21d":   row.get("univ_ret_21d_pct"),
                "fired_triggers": f.get("fired_triggers"),
            })

    total_dates = len(rows)
    avg_univ_5d  = statistics.mean(r.get("univ_ret_5d_pct")  or 0 for r in rows)
    avg_univ_21d = statistics.mean(r.get("univ_ret_21d_pct") or 0 for r in rows)

    per_case_records: list[dict] = []
    for cid, fires in sorted(case_fires.items()):
        n = len(fires)
        ret5_when  = [f["univ_5d"]  for f in fires if f["univ_5d"]  is not None]
        ret21_when = [f["univ_21d"] for f in fires if f["univ_21d"] is not None]
        avg5  = statistics.mean(ret5_when)  if ret5_when  else None
        avg21 = statistics.mean(ret21_when) if ret21_when else None
        # vs baseline drift
        edge5  = (avg5  - avg_univ_5d)  if avg5  is not None else None
        edge21 = (avg21 - avg_univ_21d) if avg21 is not None else None
        # HIT/MISS using expected direction
        outcomes_5d  = [_classify_pnl(cid, f["univ_5d"])  for f in fires
                         if f["univ_5d"]  is not None]
        outcomes_21d = [_classify_pnl(cid, f["univ_21d"]) for f in fires
                         if f["univ_21d"] is not None]
        hit5  = (outcomes_5d.count("HIT")  / max(len(outcomes_5d), 1)
                  if outcomes_5d else None)
        hit21 = (outcomes_21d.count("HIT") / max(len(outcomes_21d), 1)
                  if outcomes_21d else None)
        per_case_records.append({
            "case_id": cid,
            "expected_direction": CASE_EXPECTED_DIRECTION.get(cid, "?"),
            "n_fires": n,
            "fire_rate_pct": n / total_dates * 100,
            "avg_univ_5d_when_fired_pct":  avg5,
            "avg_univ_21d_when_fired_pct": avg21,
            "edge_vs_drift_5d_pct":  edge5,
            "edge_vs_drift_21d_pct": edge21,
            "hit_rate_5d":  hit5,
            "hit_rate_21d": hit21,
        })
    per_case_records.sort(key=lambda r: r["n_fires"], reverse=True)
    (OUT_DIR / "backtest_per_case.json").write_text(
        json.dumps(per_case_records, indent=2, default=str), encoding="utf-8")

    # ---- 2. Per-sector-overlay metrics ----------------------------------
    # For each (case, sector, action) appearing in any overlay log, score:
    # how many times it triggered, what was the sector's avg 5d/21d return
    # (vs universe), and was the action correct (downgrade -> sector
    # underperformed, upgrade -> outperformed).
    overlay_events: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        univ5 = row.get("univ_ret_5d_pct") or 0
        univ21 = row.get("univ_ret_21d_pct") or 0
        sec5 = row.get("sector_ret_5d_pct") or {}
        sec21 = row.get("sector_ret_21d_pct") or {}
        for entry in row.get("playbook_overlay_log") or []:
            cid = entry.get("case_id")
            for ch in entry.get("changes") or []:
                via = ch.get("via", "")
                if via.startswith("sector_overlay:"):
                    parts = via.split(":")
                    if len(parts) >= 3:
                        sector = parts[1]
                        action = parts[2]
                        s5  = sec5.get(sector)
                        s21 = sec21.get(sector)
                        overlay_events[(cid, sector, action)].append({
                            "as_of": row["as_of"],
                            "sec_5d":  s5,
                            "sec_21d": s21,
                            "vs_univ_5d":  (s5  - univ5)  if s5  is not None else None,
                            "vs_univ_21d": (s21 - univ21) if s21 is not None else None,
                        })

    per_sector_records: list[dict] = []
    for (cid, sector, action), evs in sorted(overlay_events.items()):
        n = len(evs)
        avg_vs5  = statistics.mean(e["vs_univ_5d"]  for e in evs
                                     if e["vs_univ_5d"]  is not None) if evs else None
        avg_vs21 = statistics.mean(e["vs_univ_21d"] for e in evs
                                     if e["vs_univ_21d"] is not None) if evs else None
        # "Correct" means: downgrade -> sector underperformed (negative),
        # upgrade -> sector outperformed (positive).
        if action == "downgrade_one":
            correct = sum(1 for e in evs
                          if e["vs_univ_5d"] is not None and e["vs_univ_5d"] < 0)
        else:  # upgrade_one
            correct = sum(1 for e in evs
                          if e["vs_univ_5d"] is not None and e["vs_univ_5d"] > 0)
        n_with_data = sum(1 for e in evs if e["vs_univ_5d"] is not None)
        accuracy = correct / max(n_with_data, 1) if n_with_data else None
        per_sector_records.append({
            "case_id": cid,
            "sector":  sector,
            "action":  action,
            "n_fires": n,
            "avg_sector_vs_univ_5d_pct":  avg_vs5,
            "avg_sector_vs_univ_21d_pct": avg_vs21,
            "directional_accuracy_5d":    accuracy,
            "n_with_5d_data":             n_with_data,
        })
    per_sector_records.sort(key=lambda r: r["n_fires"], reverse=True)
    (OUT_DIR / "backtest_per_sector_overlay.json").write_text(
        json.dumps(per_sector_records, indent=2, default=str), encoding="utf-8")

    # ---- 3. Aggregate portfolio metrics --------------------------------
    total_baseline_5d  = sum(r.get("pnl_baseline_5d_pct")  or 0 for r in rows)
    total_overlay_5d   = sum(r.get("pnl_overlay_5d_pct")   or 0 for r in rows)
    total_baseline_21d = sum(r.get("pnl_baseline_21d_pct") or 0 for r in rows)
    total_overlay_21d  = sum(r.get("pnl_overlay_21d_pct")  or 0 for r in rows)

    # Drawdown of overlay portfolio (cumulative)
    cum_baseline = 0.0
    cum_overlay  = 0.0
    peak_b = peak_o = 0.0
    dd_b = dd_o = 0.0
    for r in rows:
        cum_baseline += r.get("pnl_baseline_5d_pct") or 0
        cum_overlay  += r.get("pnl_overlay_5d_pct")  or 0
        peak_b = max(peak_b, cum_baseline)
        peak_o = max(peak_o, cum_overlay)
        dd_b = min(dd_b, cum_baseline - peak_b)
        dd_o = min(dd_o, cum_overlay  - peak_o)

    fires_hist = Counter(r.get("n_analogues") or 0 for r in rows)
    n_zero_fire = fires_hist.get(0, 0)
    # GAP weeks: zero fires AND universe down >= 3% (5d)
    gap_weeks = [r for r in rows
                 if (r.get("n_analogues") or 0) == 0
                 and (r.get("univ_ret_5d_pct") or 0) <= -3.0]
    # Big up weeks where bullish cases didn't fire
    bullish_ids = {cid for cid, d in CASE_EXPECTED_DIRECTION.items() if d == "UP"}
    missed_up_weeks = []
    for r in rows:
        if (r.get("univ_ret_5d_pct") or 0) >= 3.0:
            fired_ids = {f["id"] for f in r.get("fired") or []}
            if not (fired_ids & bullish_ids):
                missed_up_weeks.append({
                    "as_of": r["as_of"],
                    "univ_5d_pct": r["univ_ret_5d_pct"],
                    "fired": list(fired_ids),
                })

    summary = {
        "n_dates": total_dates,
        "avg_univ_ret_5d_pct":   avg_univ_5d,
        "avg_univ_ret_21d_pct":  avg_univ_21d,
        "sum_baseline_5d_pct":   total_baseline_5d,
        "sum_overlay_5d_pct":    total_overlay_5d,
        "sum_baseline_21d_pct":  total_baseline_21d,
        "sum_overlay_21d_pct":   total_overlay_21d,
        "edge_5d_total_pct":     total_overlay_5d - total_baseline_5d,
        "edge_21d_total_pct":    total_overlay_21d - total_baseline_21d,
        "max_drawdown_baseline_pct": dd_b,
        "max_drawdown_overlay_pct":  dd_o,
        "fires_histogram":       dict(fires_hist),
        "n_zero_fire_weeks":     n_zero_fire,
        "n_gap_weeks":           len(gap_weeks),
        "n_missed_up_weeks":     len(missed_up_weeks),
        "gap_weeks_sample":      gap_weeks[:10],
        "missed_up_weeks_sample": missed_up_weeks[:10],
    }
    (OUT_DIR / "backtest_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # ---- 4. Markdown report -------------------------------------------
    md = ["# Playbook + Overlay Backtest Report", ""]
    md.append(f"Window: {rows[0]['as_of']} → {rows[-1]['as_of']}  "
              f"({total_dates} weekly samples)")
    md.append("")
    md.append("## Portfolio metrics (sum of weekly P&L)")
    md.append("")
    md.append("| Metric | Baseline (all-HOLD eq-wt) | With Overlay | Edge |")
    md.append("|--------|--------------------------|--------------|------|")
    md.append(f"| Σ 5d  return | {total_baseline_5d:+.2f}% | {total_overlay_5d:+.2f}% | "
              f"{total_overlay_5d - total_baseline_5d:+.2f}% |")
    md.append(f"| Σ 21d return | {total_baseline_21d:+.2f}% | {total_overlay_21d:+.2f}% | "
              f"{total_overlay_21d - total_baseline_21d:+.2f}% |")
    md.append(f"| Max drawdown (cum 5d) | {dd_b:.2f}% | {dd_o:.2f}% | "
              f"{dd_o - dd_b:+.2f}% |")
    md.append("")
    md.append(f"Average universe forward return:  5d={avg_univ_5d:+.3f}%, "
              f"21d={avg_univ_21d:+.3f}%")
    md.append("")
    md.append("## Coverage")
    md.append(f"- Weeks with **zero** fires:  {n_zero_fire} ({n_zero_fire/total_dates*100:.0f}%)")
    md.append(f"- **GAP weeks** (zero fires AND universe -3% 5d): {len(gap_weeks)}")
    md.append(f"- **MISSED-UP weeks** (universe +3% 5d, no bullish case fired): {len(missed_up_weeks)}")
    md.append("")

    md.append("## Per-case scoreboard (sorted by fire count)")
    md.append("")
    md.append("| Case | Dir | Fires | %  | univ5d-when-fired | edge-5d | hit-5d | hit-21d |")
    md.append("|------|-----|-------|----|-------------------|---------|--------|---------|")
    for r in per_case_records[:60]:
        md.append(
            f"| {r['case_id']} | {r['expected_direction']:<5} | "
            f"{r['n_fires']:>4} | {r['fire_rate_pct']:>4.1f}% | "
            f"{(r['avg_univ_5d_when_fired_pct']  or 0):+5.2f}% | "
            f"{(r['edge_vs_drift_5d_pct']       or 0):+5.2f}% | "
            f"{(r['hit_rate_5d']  or 0)*100:>3.0f}% | "
            f"{(r['hit_rate_21d'] or 0)*100:>3.0f}% |"
        )
    md.append("")

    md.append("## Sector overlay accuracy (sorted by fire count)")
    md.append("")
    md.append("| Case | Sector | Action | Fires | sec-vs-univ 5d | accuracy 5d |")
    md.append("|------|--------|--------|-------|----------------|-------------|")
    for r in per_sector_records[:60]:
        md.append(
            f"| {r['case_id']} | {r['sector']} | {r['action']} | {r['n_fires']:>3} | "
            f"{(r['avg_sector_vs_univ_5d_pct'] or 0):+5.2f}% | "
            f"{(r['directional_accuracy_5d']   or 0)*100:>3.0f}% |"
        )
    md.append("")

    md.append("## GAP weeks (drawdown -3% 5d, no playbook fire)")
    if gap_weeks:
        md.append("")
        md.append("| Date | Univ 5d | Univ 21d |")
        md.append("|------|---------|----------|")
        for r in gap_weeks[:30]:
            md.append(f"| {r['as_of']} | {r['univ_ret_5d_pct']:+.2f}% | "
                      f"{r['univ_ret_21d_pct']:+.2f}% |")
    md.append("")

    md.append("## MISSED-UP weeks (rally +3% 5d, no bullish case fired)")
    if missed_up_weeks:
        md.append("")
        md.append("| Date | Univ 5d | Fired (non-bullish) |")
        md.append("|------|---------|---------------------|")
        for r in missed_up_weeks[:30]:
            md.append(f"| {r['as_of']} | {r['univ_5d_pct']:+.2f}% | {r['fired']} |")
    md.append("")

    md_path = OUT_DIR / "backtest_report.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[analyse] wrote {md_path}")
    print(f"[analyse] wrote {OUT_DIR / 'backtest_per_case.json'}")
    print(f"[analyse] wrote {OUT_DIR / 'backtest_per_sector_overlay.json'}")
    print(f"[analyse] wrote {OUT_DIR / 'backtest_summary.json'}")
    print()
    print("Quick summary:")
    print(f"  n_dates: {total_dates}")
    print(f"  baseline 5d sum: {total_baseline_5d:+.2f}%")
    print(f"  overlay  5d sum: {total_overlay_5d:+.2f}%  edge: {total_overlay_5d - total_baseline_5d:+.2f}%")
    print(f"  zero-fire weeks: {n_zero_fire} | gap-down weeks: {len(gap_weeks)} | missed-up weeks: {len(missed_up_weeks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
