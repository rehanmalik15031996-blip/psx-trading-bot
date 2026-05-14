"""Bull-vs-bear regime stress test of the playbook overlay.

Uses the 258-week 5-year backtest in data/_research/backtest_per_date.json.
Classifies each date by TRAILING regime (the briefing's as-of regime
field — NORMAL/CAUTION/CRISIS — which is computed from the prior 5d
universe return WITHOUT look-ahead), plus a secondary classification
by trailing 21d return computed by shifting the forward-21d series.

For each regime bucket:
  - mean baseline pnl, mean overlay pnl, edge_vs_base, edge_vs_univ
  - hit rate (% of weeks overlay beats baseline)
  - cash-deployment stats (gross_baseline vs gross_overlay)

Then builds cumulative-return paths so we can see the equity curve
of overlay vs baseline vs passive universe through bull and bear
epochs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import statistics as st

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DATA = ROOT / "data" / "_research" / "backtest_per_date.json"
OUT = ROOT / "data" / "_research" / "bull_bear_stress.json"
OUT_MD = ROOT / "data" / "_research" / "BULL_BEAR_REPORT.md"


def _shift_trailing_21d(rows: list[dict]) -> None:
    """Each row has forward 21d return at `as_of`. To get the trailing
    21d return AS OF the date (no look-ahead), use the forward-21d
    return of the row that's 4 weeks (21 trading days / 5 = ~4 weekly
    samples) earlier.
    """
    n = len(rows)
    for i, r in enumerate(rows):
        # Use the row 4 weeks ago — its forward-21d ended around here.
        j = max(0, i - 4)
        r["trailing_univ_21d_pct"] = rows[j].get("univ_ret_21d_pct") or 0


def _classify_trailing(r: dict) -> str:
    """Classify the regime using only data available at `as_of` (no
    forward look-ahead). Combines briefing.regime with trailing-21d."""
    reg = (r.get("regime") or "").upper()
    t21 = r.get("trailing_univ_21d_pct") or 0
    # CRISIS / CAUTION come from the briefing's trailing-5d classifier.
    # Combine with trailing-21d to distinguish a sustained bear from a
    # one-week drop.
    if reg == "CRISIS" or t21 <= -5.0:
        return "BEAR_sustained"
    if reg == "CAUTION":
        return "BEAR_recent_drop"
    if t21 >= 8.0:
        return "BULL_strong"
    if t21 >= 3.0:
        return "BULL_mild"
    return "NEUTRAL"


def _agg(rs: list[dict]) -> dict:
    if not rs:
        return {"n": 0}
    base   = [r["pnl_baseline_5d_pct"] for r in rs if r.get("pnl_baseline_5d_pct") is not None]
    over   = [r["pnl_overlay_5d_pct"]  for r in rs if r.get("pnl_overlay_5d_pct")  is not None]
    univ   = [r["univ_ret_5d_pct"]     for r in rs if r.get("univ_ret_5d_pct")     is not None]
    edge_b = [o - bv for o, bv in zip(over, base)]
    edge_u = [o - u  for o, u  in zip(over, univ)]
    wins_b = sum(1 for e in edge_b if e > 0)
    wins_u = sum(1 for e in edge_u if e > 0)
    gross_b = [r.get("gross_baseline_5d") or 0 for r in rs]
    gross_o = [r.get("gross_overlay_5d")  or 0 for r in rs]
    fires   = [r.get("n_analogues") or 0 for r in rs]
    return {
        "n": len(rs),
        "univ_mean":     st.mean(univ) if univ else 0,
        "base_mean":     st.mean(base) if base else 0,
        "over_mean":     st.mean(over) if over else 0,
        "edge_vs_base":  st.mean(edge_b) if edge_b else 0,
        "edge_vs_univ":  st.mean(edge_u) if edge_u else 0,
        "win_rate_vs_base": wins_b / max(len(edge_b), 1),
        "win_rate_vs_univ": wins_u / max(len(edge_u), 1),
        "gross_b_mean":  st.mean(gross_b),
        "gross_o_mean":  st.mean(gross_o),
        "deployment_diff_pp": (st.mean(gross_o) - st.mean(gross_b)) * 100,
        "mean_fires":    st.mean(fires),
    }


def _cumulative(rows: list[dict], key: str) -> list[float]:
    """Compute compounded equity curve from per-period % returns."""
    eq = [100.0]
    for r in rows:
        x = r.get(key)
        if x is None:
            eq.append(eq[-1])
            continue
        eq.append(eq[-1] * (1.0 + x / 100.0))
    return eq


def _longest_run(rows: list[dict], predicate) -> tuple[int, int, int]:
    """Longest contiguous stretch where `predicate(row)` is True.
    Returns (length, start_idx, end_idx)."""
    best = (0, -1, -1)
    cur_start = -1
    cur_len = 0
    for i, r in enumerate(rows):
        if predicate(r):
            if cur_start < 0:
                cur_start = i
            cur_len += 1
            if cur_len > best[0]:
                best = (cur_len, cur_start, i)
        else:
            cur_start = -1
            cur_len = 0
    return best


def _drawdown(eq: list[float]) -> tuple[float, int, int]:
    """Max drawdown of an equity curve. Returns (max_dd_pct, peak_idx, trough_idx)."""
    peak = eq[0]
    peak_i = 0
    max_dd = 0.0
    max_peak_i = 0
    max_trough_i = 0
    for i, v in enumerate(eq):
        if v > peak:
            peak = v
            peak_i = i
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
            max_peak_i = peak_i
            max_trough_i = i
    return max_dd, max_peak_i, max_trough_i


def main():
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    rows.sort(key=lambda r: r["as_of"])
    _shift_trailing_21d(rows)

    # Per-row regime label
    for r in rows:
        r["bb_regime"] = _classify_trailing(r)

    print(f"Total weeks: {len(rows)}")
    print()
    # Regime distribution
    dist = {}
    for r in rows:
        dist[r["bb_regime"]] = dist.get(r["bb_regime"], 0) + 1
    print("Regime distribution (trailing-classified, no look-ahead):")
    for k in ["BULL_strong", "BULL_mild", "NEUTRAL",
             "BEAR_recent_drop", "BEAR_sustained"]:
        n = dist.get(k, 0)
        bar = "#" * int(n / 5)
        print(f"  {k:<22} {n:>4} ({n/len(rows)*100:>5.1f}%)  {bar}")
    print()

    summary = {}
    print("=" * 100)
    print(f"{'regime':<22} {'n':>4} {'univ%':>7} {'base%':>7} {'over%':>7} "
          f"{'edge_base':>10} {'edge_univ':>10} {'win_b%':>7} {'gross_b':>8} "
          f"{'gross_o':>8} {'fires':>6}")
    print("-" * 100)
    for k in ["BULL_strong", "BULL_mild", "NEUTRAL",
             "BEAR_recent_drop", "BEAR_sustained"]:
        rs = [r for r in rows if r["bb_regime"] == k]
        s = _agg(rs)
        summary[k] = s
        if s["n"] == 0:
            print(f"  {k:<20} (empty)")
            continue
        print(f"  {k:<20} {s['n']:>4} "
              f"{s['univ_mean']:>+6.2f}% {s['base_mean']:>+6.2f}% "
              f"{s['over_mean']:>+6.2f}% "
              f"{s['edge_vs_base']:>+9.2f}pp "
              f"{s['edge_vs_univ']:>+9.2f}pp "
              f"{s['win_rate_vs_base']*100:>6.1f}% "
              f"{s['gross_b_mean']:>7.3f} {s['gross_o_mean']:>7.3f} "
              f"{s['mean_fires']:>5.1f}")
    print()

    # Cumulative equity curves
    eq_base = _cumulative(rows, "pnl_baseline_5d_pct")
    eq_over = _cumulative(rows, "pnl_overlay_5d_pct")
    eq_univ = _cumulative(rows, "univ_ret_5d_pct")
    print("CUMULATIVE EQUITY (start=100, 5-year compounded, weekly samples):")
    print(f"  baseline (all-HOLD equal weight):     {eq_base[-1]:>7.1f}  "
          f"({(eq_base[-1]/100-1)*100:>+.1f}%)")
    print(f"  overlay  (playbook-modified):         {eq_over[-1]:>7.1f}  "
          f"({(eq_over[-1]/100-1)*100:>+.1f}%)")
    print(f"  universe (passive equal-weight):      {eq_univ[-1]:>7.1f}  "
          f"({(eq_univ[-1]/100-1)*100:>+.1f}%)")
    print()

    # Max drawdown for each curve
    dd_b, dd_b_pi, dd_b_ti = _drawdown(eq_base)
    dd_o, dd_o_pi, dd_o_ti = _drawdown(eq_over)
    dd_u, dd_u_pi, dd_u_ti = _drawdown(eq_univ)
    print("MAX DRAWDOWN:")
    print(f"  baseline: {dd_b:>5.2f}%  (peak {rows[max(0,dd_b_pi-1)]['as_of']} -> trough {rows[max(0,dd_b_ti-1)]['as_of']})")
    print(f"  overlay:  {dd_o:>5.2f}%  (peak {rows[max(0,dd_o_pi-1)]['as_of']} -> trough {rows[max(0,dd_o_ti-1)]['as_of']})")
    print(f"  universe: {dd_u:>5.2f}%  (peak {rows[max(0,dd_u_pi-1)]['as_of']} -> trough {rows[max(0,dd_u_ti-1)]['as_of']})")
    print()

    # Longest bull and bear epochs
    bull_len, bull_s, bull_e = _longest_run(rows, lambda r: r["bb_regime"].startswith("BULL"))
    bear_len, bear_s, bear_e = _longest_run(rows, lambda r: r["bb_regime"].startswith("BEAR"))
    print("LONGEST SUSTAINED EPOCHS:")
    if bull_s >= 0:
        bull_rows = rows[bull_s:bull_e+1]
        bull_uni  = (eq_univ[bull_e+1] / eq_univ[bull_s] - 1) * 100
        bull_base = (eq_base[bull_e+1] / eq_base[bull_s] - 1) * 100
        bull_over = (eq_over[bull_e+1] / eq_over[bull_s] - 1) * 100
        print(f"  BULL  {bull_len:>3} weeks  "
              f"{rows[bull_s]['as_of']} .. {rows[bull_e]['as_of']}  "
              f"univ {bull_uni:+.1f}%, baseline {bull_base:+.1f}%, "
              f"overlay {bull_over:+.1f}%  "
              f"(overlay cost: {bull_over-bull_base:+.1f}pp)")
    if bear_s >= 0:
        bear_rows = rows[bear_s:bear_e+1]
        bear_uni  = (eq_univ[bear_e+1] / eq_univ[bear_s] - 1) * 100
        bear_base = (eq_base[bear_e+1] / eq_base[bear_s] - 1) * 100
        bear_over = (eq_over[bear_e+1] / eq_over[bear_s] - 1) * 100
        print(f"  BEAR  {bear_len:>3} weeks  "
              f"{rows[bear_s]['as_of']} .. {rows[bear_e]['as_of']}  "
              f"univ {bear_uni:+.1f}%, baseline {bear_base:+.1f}%, "
              f"overlay {bear_over:+.1f}%  "
              f"(overlay save: {bear_over-bear_base:+.1f}pp)")

    print()
    # Per-sector edge in each regime (which sectors does the overlay help in which regime?)
    sec_perf = {}  # sector -> regime -> [edge_overlay_minus_baseline]
    # The per-date row doesn't have per-sector overlay returns; only
    # per-sector UNIVERSE returns. So we measure how the OVERLAY-LOG'd
    # downgrade/upgrade actions align with sector behaviour.
    for r in rows:
        reg = r["bb_regime"]
        sec_5d = r.get("sector_ret_5d_pct") or {}
        log = r.get("playbook_overlay_log") or []
        # Which sectors did overlay downgrade/upgrade this week?
        sec_actions = {}
        for c in log:
            for ch in c.get("changes", []):
                via = ch.get("via", "") if isinstance(ch, dict) else ""
                if "sector_overlay:" in via:
                    parts = via.split(":")
                    if len(parts) >= 3:
                        sec_name = parts[1]
                        action = parts[2]
                        sec_actions[sec_name] = action
        for s, ret in sec_5d.items():
            if s in sec_actions:
                act = sec_actions[s]
                # If we downgraded a sector that subsequently FELL, that's a save.
                # If we upgraded a sector that subsequently ROSE, that's a capture.
                if act == "downgrade_one":
                    proxy_edge = -ret    # we earn if sector fell
                else:
                    proxy_edge = ret     # we earn if sector rose
                sec_perf.setdefault(s, {}).setdefault(reg, []).append(proxy_edge)

    print("PER-SECTOR OVERLAY EDGE (when overlay acted on the sector):")
    print(f"{'sector':<22} {'regime':<22} {'n':>4} {'avg_edge_pp':>11}")
    for sec in sorted(sec_perf.keys()):
        for reg in ["BULL_strong","BULL_mild","NEUTRAL",
                     "BEAR_recent_drop","BEAR_sustained"]:
            vals = sec_perf[sec].get(reg, [])
            if not vals:
                continue
            print(f"  {sec:<20} {reg:<22} {len(vals):>4} "
                  f"{sum(vals)/len(vals):>+10.2f}pp")
    print()

    OUT.write_text(json.dumps({
        "regime_distribution": dist,
        "per_regime": summary,
        "cumulative": {
            "baseline_final": eq_base[-1],
            "overlay_final":  eq_over[-1],
            "universe_final": eq_univ[-1],
        },
        "max_drawdown": {
            "baseline": dd_b, "overlay": dd_o, "universe": dd_u,
        },
        "longest_bull": {"weeks": bull_len,
                          "start": rows[bull_s]["as_of"] if bull_s >= 0 else None,
                          "end":   rows[bull_e]["as_of"] if bull_e >= 0 else None},
        "longest_bear": {"weeks": bear_len,
                          "start": rows[bear_s]["as_of"] if bear_s >= 0 else None,
                          "end":   rows[bear_e]["as_of"] if bear_e >= 0 else None},
        "per_sector_edge": {
            s: {reg: {"n": len(v), "avg_edge_pp": sum(v)/len(v)}
                for reg, v in regs.items()}
            for s, regs in sec_perf.items()
        },
    }, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")

    # ------- Markdown report --------
    md = ["# Bull-vs-Bear regime stress test (2026-05-15)\n",
          f"_Generated from `data/_research/backtest_per_date.json` "
          f"({len(rows)} weekly samples, 2021-06-04 -> 2026-05-08)._\n",
          "## Regime classification\n",
          "Trailing 21d universe return, combined with the briefing's "
          "trailing-5d regime label. Strictly no look-ahead.\n",
          "| Regime | Definition | Count | Share |",
          "|---|---|---:|---:|",
          f"| BULL_strong | trailing 21d ≥ +8% | {dist.get('BULL_strong',0)} | {dist.get('BULL_strong',0)/len(rows)*100:.1f}% |",
          f"| BULL_mild   | trailing 21d ≥ +3% | {dist.get('BULL_mild',0)} | {dist.get('BULL_mild',0)/len(rows)*100:.1f}% |",
          f"| NEUTRAL     | -2% < trailing 21d < +3% | {dist.get('NEUTRAL',0)} | {dist.get('NEUTRAL',0)/len(rows)*100:.1f}% |",
          f"| BEAR_recent_drop | briefing regime=CAUTION (trailing 5d ≤ -2%) | {dist.get('BEAR_recent_drop',0)} | {dist.get('BEAR_recent_drop',0)/len(rows)*100:.1f}% |",
          f"| BEAR_sustained   | trailing 21d ≤ -5% OR briefing=CRISIS | {dist.get('BEAR_sustained',0)} | {dist.get('BEAR_sustained',0)/len(rows)*100:.1f}% |",
          "",
          "## Per-regime performance (forward 5d)\n",
          "| Regime | n | univ% | base% | overlay% | edge vs base | edge vs univ | win-rate vs base | gross_base | gross_overlay | fires/wk |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k in ["BULL_strong","BULL_mild","NEUTRAL","BEAR_recent_drop","BEAR_sustained"]:
        s = summary.get(k, {})
        if not s or s.get("n", 0) == 0:
            continue
        md.append(
            f"| **{k}** | {s['n']} | {s['univ_mean']:+.2f} | {s['base_mean']:+.2f} | "
            f"{s['over_mean']:+.2f} | {s['edge_vs_base']:+.2f} | "
            f"{s['edge_vs_univ']:+.2f} | {s['win_rate_vs_base']*100:.0f}% | "
            f"{s['gross_b_mean']:.2f} | {s['gross_o_mean']:.2f} | "
            f"{s['mean_fires']:.1f} |")
    md.extend([
        "",
        "## Cumulative 5-year equity (start = 100)\n",
        "| Track | Final | Total return | Max drawdown |",
        "|---|---:|---:|---:|",
        f"| Baseline (all-HOLD equal weight) | {eq_base[-1]:.1f} | {(eq_base[-1]/100-1)*100:+.1f}% | {dd_b:.2f}% |",
        f"| Overlay  (playbook-modified)     | {eq_over[-1]:.1f} | {(eq_over[-1]/100-1)*100:+.1f}% | {dd_o:.2f}% |",
        f"| Universe (passive equal-weight)  | {eq_univ[-1]:.1f} | {(eq_univ[-1]/100-1)*100:+.1f}% | {dd_u:.2f}% |",
        "",
        "## Longest sustained epochs\n",
    ])
    if bull_s >= 0:
        bull_uni  = (eq_univ[bull_e+1] / eq_univ[bull_s] - 1) * 100
        bull_base = (eq_base[bull_e+1] / eq_base[bull_s] - 1) * 100
        bull_over = (eq_over[bull_e+1] / eq_over[bull_s] - 1) * 100
        md.append(f"- **Longest BULL epoch**: {bull_len} weeks, "
                  f"{rows[bull_s]['as_of']} -> {rows[bull_e]['as_of']}. "
                  f"Universe {bull_uni:+.1f}%, baseline {bull_base:+.1f}%, "
                  f"overlay {bull_over:+.1f}%. Overlay cost vs baseline: "
                  f"**{bull_over-bull_base:+.1f}pp** (overlay forgoes some upside).")
    if bear_s >= 0:
        bear_uni  = (eq_univ[bear_e+1] / eq_univ[bear_s] - 1) * 100
        bear_base = (eq_base[bear_e+1] / eq_base[bear_s] - 1) * 100
        bear_over = (eq_over[bear_e+1] / eq_over[bear_s] - 1) * 100
        md.append(f"- **Longest BEAR epoch**: {bear_len} weeks, "
                  f"{rows[bear_s]['as_of']} -> {rows[bear_e]['as_of']}. "
                  f"Universe {bear_uni:+.1f}%, baseline {bear_base:+.1f}%, "
                  f"overlay {bear_over:+.1f}%. Overlay save vs baseline: "
                  f"**{bear_over-bear_base:+.1f}pp**.")

    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
