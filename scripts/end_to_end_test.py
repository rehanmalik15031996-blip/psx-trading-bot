"""End-to-end model test on the past 12 months.

Walks every Mon/Wed/Fri trading-day from 2025-05-01 to 2026-04-01
(roughly 130 dates), reconstructs the full Master Strategist briefing
for each, runs the playbook matcher in production mode (MF + macro),
and scores every fired case against the actual forward 5-day and 21-day
universe returns.

Outputs `data/_health/end_to_end_test.md` with:

* **Headline accuracy** (overall HIT / MISS / GAP / NULL split, plus
  precision and recall on significant moves).
* **Per-case attribution** -- for every case in `data/playbook/cases.json`
  show how many times it fired, mean fwd_5d / fwd_21d when it fired,
  hit-rate, and whether it ever fired (orphan cases are flagged).
* **Per-month rollup** -- to spot regime shifts (e.g. Feb-26 drawdown).
* **Storage-of-patterns sanity check** -- catalogue of every case the
  matcher actually used, plus how often per-stock vs universe-level
  triggers were the dominant driver.
* **LLM predictions log** -- if `data/predictions_log.json` has scored
  predictions, summarise direction-hit-rate, mean-error, and per-model
  breakdown.

Run once at the end of every sprint or after editing any playbook
case / signal:

    python scripts/end_to_end_test.py

The file is self-contained -- it imports from the existing
`historical_test_playbook` and `replay_briefing` modules so the
matcher path is exactly the same one production uses.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import playbook as pb
from scripts.historical_test_playbook import (
    CASE_EXPECTED_DIRECTION,
    _classify_outcome,
    run_one as _run_one_modes,
)

OUT_PATH = ROOT / "data" / "_health" / "end_to_end_test.md"
JSON_OUT = ROOT / "data" / "_health" / "end_to_end_test.json"
PREDICTIONS_LOG = ROOT / "data" / "predictions_log.json"
CASES_PATH = ROOT / "data" / "playbook" / "cases.json"


# ---------------------------------------------------------------------------
# 1) Build the date sample
# ---------------------------------------------------------------------------
def sample_dates(start: date, end: date,
                  weekdays: tuple[int, ...] = (0, 2, 4)) -> list[date]:
    """Mon / Wed / Fri inside [start, end). ~3 dates / week."""
    out: list[date] = []
    d = start
    while d < end:
        if d.weekday() in weekdays:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# 2) Score one date in production mode and capture per-case detail
# ---------------------------------------------------------------------------
def run_year(start: date, end: date) -> list[dict]:
    """Run production matcher on every sample date and gather raw rows."""
    dates = sample_dates(start, end)
    print(f"\nWalking {len(dates)} trading dates from {start} to {end} "
          f"({(end - start).days} calendar days)...")
    rows: list[dict] = []
    for i, d in enumerate(dates, 1):
        try:
            r = _run_one_modes(d.isoformat(), d, modes=["with_mf_macro"])
        except Exception as e:
            r = {"as_of": d.isoformat(), "verdict": "ERR",
                 "error": f"{type(e).__name__}: {e}"}
        rows.append(r)
        if i % 10 == 0 or i == len(dates):
            verdict_counts = Counter(x.get("verdict", "?") for x in rows)
            print(f"  {i:>3}/{len(dates)}  {d}  "
                  f"H={verdict_counts.get('HIT',0)} "
                  f"M={verdict_counts.get('MISS',0)} "
                  f"G={verdict_counts.get('GAP',0)} "
                  f"N={verdict_counts.get('NULL',0)}")
    return rows


# ---------------------------------------------------------------------------
# 3) Per-case attribution
# ---------------------------------------------------------------------------
def per_case_stats(rows: list[dict]) -> list[dict]:
    """For every case in the library, count fire count + mean returns +
    hit rate + miss rate. Cases that never fired are flagged."""
    case_lib = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    case_ids = [c["id"] for c in case_lib]
    case_meta = {c["id"]: c for c in case_lib}

    fired: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("verdict") == "ERR":
            continue
        outs = (r.get("modes") or {}).get("with_mf_macro", {}).get("case_outcomes") or []
        for o in outs:
            fired[o["id"]].append({
                "as_of": r.get("as_of"),
                "fwd_5d": r.get("fwd_5d"),
                "fwd_21d": r.get("fwd_21d"),
                "outcome": o.get("outcome"),
                "score": o.get("score"),
                "expected": o.get("expected"),
            })

    out: list[dict] = []
    for cid in case_ids:
        instances = fired.get(cid, [])
        n = len(instances)
        out_5d  = [i["fwd_5d"]  for i in instances if isinstance(i["fwd_5d"],  (int, float))]
        out_21d = [i["fwd_21d"] for i in instances if isinstance(i["fwd_21d"], (int, float))]
        hits  = sum(1 for i in instances if i["outcome"] == "HIT")
        misses = sum(1 for i in instances if i["outcome"] == "MISS")
        scored = hits + misses
        out.append({
            "id":          cid,
            "category":    case_meta[cid].get("category"),
            "expected":    CASE_EXPECTED_DIRECTION.get(cid, "?"),
            "confidence":  case_meta[cid].get("confidence"),
            "n_fired":     n,
            "n_hit":       hits,
            "n_miss":      misses,
            "hit_rate":    (hits / scored * 100) if scored else None,
            "mean_fwd_5d_pct":  (mean(out_5d)  * 100 if out_5d  else None),
            "mean_fwd_21d_pct": (mean(out_21d) * 100 if out_21d else None),
            "median_fwd_21d_pct": (median(out_21d) * 100 if out_21d else None),
        })
    out.sort(key=lambda x: (-x["n_fired"], x["id"]))
    return out


# ---------------------------------------------------------------------------
# 4) Per-month rollup
# ---------------------------------------------------------------------------
def per_month_stats(rows: list[dict]) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            d = datetime.strptime(r["as_of"], "%Y-%m-%d").date()
            by_month[f"{d.year}-{d.month:02d}"].append(r)
        except (ValueError, KeyError, TypeError):
            continue
    out: list[dict] = []
    for ym in sorted(by_month):
        rs = by_month[ym]
        c  = Counter(r.get("verdict", "?") for r in rs)
        sig = sum(1 for r in rs if r.get("significant"))
        prec = (c["HIT"] / (c["HIT"] + c["MISS"]) * 100
                if (c["HIT"] + c["MISS"]) > 0 else None)
        recall = ((sig - c["GAP"]) / sig * 100) if sig > 0 else None
        out.append({
            "ym":      ym,
            "n_dates": len(rs),
            "hit":     c["HIT"], "miss": c["MISS"],
            "gap":     c["GAP"], "null": c["NULL"],
            "err":     c.get("ERR", 0),
            "sig":     sig,
            "precision": prec,
            "recall":    recall,
        })
    return out


# ---------------------------------------------------------------------------
# 5) Predictions-log scoring (uses already-checked outcomes if present)
# ---------------------------------------------------------------------------
def predictions_summary() -> dict | None:
    if not PREDICTIONS_LOG.exists():
        return None
    try:
        data = json.loads(PREDICTIONS_LOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    preds = data.get("predictions", [])
    if not preds:
        return None
    by_dir = defaultdict(lambda: {"n": 0, "hits": 0, "ret_sum": 0.0,
                                    "abs_err_sum": 0.0, "scored": 0})
    by_model = defaultdict(lambda: {"n": 0, "hits": 0, "scored": 0})
    n_total = n_scored = n_dir_hit = 0
    abs_err_total = 0.0
    for p in preds:
        direction = (p.get("direction") or "").upper()
        model = p.get("model") or "unknown"
        outcome = p.get("outcome") or {}
        by_dir[direction]["n"]    += 1
        by_model[model]["n"]      += 1
        n_total                   += 1
        if outcome and outcome.get("direction_hit") is not None:
            n_scored               += 1
            by_dir[direction]["scored"]   += 1
            by_model[model]["scored"]     += 1
            if outcome["direction_hit"]:
                n_dir_hit                  += 1
                by_dir[direction]["hits"]  += 1
                by_model[model]["hits"]    += 1
            actual = outcome.get("actual_return_pct")
            mid    = p.get("expected_return_5d_mid_pct")
            if actual is not None and mid is not None:
                err = abs(actual - mid)
                abs_err_total                  += err
                by_dir[direction]["abs_err_sum"] += err
                by_dir[direction]["ret_sum"]    += actual
    if n_scored == 0:
        return {"n_predictions": n_total, "n_scored": 0}
    return {
        "n_predictions":     n_total,
        "n_scored":          n_scored,
        "direction_hit_pct": n_dir_hit / n_scored * 100,
        "mean_abs_error_pct": abs_err_total / n_scored,
        "by_direction": {
            d: {
                "n":          v["n"],
                "scored":     v["scored"],
                "hit_pct":    (v["hits"] / v["scored"] * 100) if v["scored"] else None,
                "mean_actual_pct": (v["ret_sum"] / v["scored"]) if v["scored"] else None,
                "mean_abs_err_pct": (v["abs_err_sum"] / v["scored"]) if v["scored"] else None,
            }
            for d, v in by_dir.items()
        },
        "by_model": {
            m: {"n": v["n"], "scored": v["scored"],
                 "hit_pct": (v["hits"] / v["scored"] * 100) if v["scored"] else None}
            for m, v in by_model.items()
        },
    }


# ---------------------------------------------------------------------------
# 6) Headline aggregation
# ---------------------------------------------------------------------------
def aggregate(rows: list[dict]) -> dict:
    c   = Counter(r.get("verdict", "?") for r in rows)
    sig = sum(1 for r in rs_no_err(rows) if r.get("significant"))
    n   = sum(1 for r in rs_no_err(rows))
    prec   = (c["HIT"] / (c["HIT"] + c["MISS"]) * 100
              if (c["HIT"] + c["MISS"]) > 0 else None)
    recall = ((sig - c["GAP"]) / sig * 100) if sig > 0 else None
    fired_dates = sum(1 for r in rs_no_err(rows) if (r.get("n_analogues") or 0) > 0)
    return {
        "n_dates": n, "n_significant": sig, "n_dates_with_match": fired_dates,
        "hit": c["HIT"], "miss": c["MISS"], "gap": c["GAP"], "null": c["NULL"],
        "err": c.get("ERR", 0),
        "precision": prec, "recall": recall,
        "match_coverage_pct": (fired_dates / n * 100) if n else None,
    }


def rs_no_err(rows: list[dict]):
    for r in rows:
        if r.get("verdict") != "ERR":
            yield r


# ---------------------------------------------------------------------------
# 7) Markdown report
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 7b) "Follow the playbook" P&L vs buy-and-hold
# ---------------------------------------------------------------------------
def simulate_strategy_pnl(rows: list[dict],
                            start: date, end: date) -> dict:
    """Simulate a defensive 'follow the playbook' strategy on weekly windows.

    Strategy rules (deliberately simple so results are interpretable):
      * Default position: 100% long the universe (passive).
      * Override to **cash** for the next 5 trading days if any case with
        expected direction DOWN or FLAT fires on the Friday close.
      * Stay long in UP / no-signal / NULL states.

    Sampling: only Fridays, so each ``fwd_5d`` covers the next Mon-Fri and
    consecutive windows do not overlap. Compounds returns to give a single
    cumulative number comparable to a "buy-and-hold the universe" benchmark.

    The point is NOT to claim alpha (one strategy on one slice is not a
    significance test), but to give the analyst a *concrete* sense of
    whether the playbook's defensive triggers help or hurt vs doing
    nothing.
    """
    weekly = [r for r in rows
                if r.get("verdict") != "ERR"
                and isinstance(r.get("fwd_5d"), (int, float))
                and date.fromisoformat(r["as_of"]).weekday() == 4]
    if not weekly:
        return {"n_windows": 0}

    sys_cum = 1.0
    bh_cum  = 1.0
    n_long  = 0
    n_hits  = 0
    n_avoided_dd = 0
    weekly_records: list[dict] = []
    for r in weekly:
        fwd = float(r["fwd_5d"])
        outs = ((r.get("modes") or {}).get("with_mf_macro", {})
                  .get("case_outcomes") or [])
        case_dirs = [CASE_EXPECTED_DIRECTION.get(o["id"], "?") for o in outs]
        bearish = any(d in ("DOWN", "FLAT") for d in case_dirs)
        sys_ret = 0.0 if bearish else fwd
        sys_cum *= (1.0 + sys_ret)
        bh_cum  *= (1.0 + fwd)
        if not bearish:
            n_long += 1
        if sys_ret > 0:
            n_hits += 1
        if bearish and fwd < 0:
            n_avoided_dd += 1
        weekly_records.append({
            "date": r["as_of"],
            "position": "cash" if bearish else "long",
            "fwd_5d_pct": fwd * 100.0,
            "sys_ret_pct": sys_ret * 100.0,
            "bh_ret_pct":  fwd * 100.0,
            "case_signals": case_dirs,
        })

    n = len(weekly)
    return {
        "n_windows": n,
        "system_cum_pct": (sys_cum - 1.0) * 100.0,
        "bh_cum_pct":     (bh_cum  - 1.0) * 100.0,
        "alpha_pct":      (sys_cum - bh_cum) * 100.0,
        "pct_time_long":  n_long / n * 100.0,
        "n_cash_weeks":   n - n_long,
        "n_avoided_drawdown_weeks": n_avoided_dd,
        "system_hit_rate_pct": n_hits / n * 100.0,
        "weekly_records": weekly_records,
    }


# ---------------------------------------------------------------------------
# 7c) Render the markdown report
# ---------------------------------------------------------------------------
def render(rows: list[dict], headline: dict, by_case: list[dict],
            by_month: list[dict], preds: dict | None,
            pnl: dict | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# End-to-end model test\n\n")
    lines.append(f"_Run at {datetime.now().isoformat(timespec='seconds')}_\n\n")
    lines.append(f"Production-mode matcher (MF + macro) walked {headline['n_dates']} "
                  f"trading dates ({rows[0]['as_of']} -> {rows[-1]['as_of']}, "
                  f"~Mon/Wed/Fri sampling). Every fired case was scored against "
                  f"the actual forward 5d / 21d universe returns.\n\n")

    lines.append("## Headline accuracy\n\n")
    lines.append("| Metric | Value |\n|---|---|\n")
    lines.append(f"| Trading dates evaluated | {headline['n_dates']} |\n")
    lines.append(f"| Significant moves (\\|fwd_5d\\| >= 4% OR \\|fwd_21d\\| >= 8%) "
                  f"| {headline['n_significant']} |\n")
    lines.append(f"| Dates where the matcher fired >=1 case "
                  f"| {headline['n_dates_with_match']} "
                  f"({headline['match_coverage_pct']:.1f}%) |\n")
    lines.append(f"| **HIT** (case fired AND direction matched) | "
                  f"{headline['hit']} |\n")
    lines.append(f"| **MISS** (case fired AND direction wrong) | "
                  f"{headline['miss']} |\n")
    lines.append(f"| **GAP** (significant move with NO case fired) | "
                  f"{headline['gap']} |\n")
    lines.append(f"| **NULL** (quiet day, no case fired -- correct) | "
                  f"{headline['null']} |\n")
    lines.append(f"| Errors / replay crashes | {headline['err']} |\n")
    if headline['precision'] is not None:
        lines.append(f"| **Directional precision when matcher fires** | "
                      f"{headline['precision']:.1f}% |\n")
    if headline['recall'] is not None:
        lines.append(f"| **Recall on significant moves** | "
                      f"{headline['recall']:.1f}% "
                      f"({headline['n_significant'] - headline['gap']}/"
                      f"{headline['n_significant']}) |\n")
    lines.append("\n")

    # ---------- per-case attribution ----------
    lines.append("## Per-case attribution (storage-of-patterns audit)\n\n")
    lines.append("Cases ordered by fire count. Hit-rate is for the case in "
                  "isolation (NOT the verdict, which is computed at the "
                  "date level).\n\n")
    lines.append("| Case | Cat | Conf | Exp | Fired | HIT | MISS | "
                  "Hit rate | Mean fwd 5d | Mean fwd 21d | Median fwd 21d |\n")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
    for c in by_case:
        hr = (f"{c['hit_rate']:.0f}%" if c['hit_rate'] is not None else "n/a")
        m5 = (f"{c['mean_fwd_5d_pct']:+.1f}%"
              if c['mean_fwd_5d_pct'] is not None else "n/a")
        m21 = (f"{c['mean_fwd_21d_pct']:+.1f}%"
                if c['mean_fwd_21d_pct'] is not None else "n/a")
        med21 = (f"{c['median_fwd_21d_pct']:+.1f}%"
                  if c['median_fwd_21d_pct'] is not None else "n/a")
        lines.append(f"| `{c['id']}` | {c['category'][:6] if c['category'] else ''} | "
                      f"{c['confidence'] or ''} | {c['expected']} | "
                      f"{c['n_fired']} | {c['n_hit']} | {c['n_miss']} | "
                      f"{hr} | {m5} | {m21} | {med21} |\n")
    lines.append("\n")

    n_orphan = sum(1 for c in by_case if c['n_fired'] == 0)
    n_low    = sum(1 for c in by_case
                     if c['n_fired'] > 0 and c['n_fired'] < 3)
    lines.append(f"**Orphan cases** (never fired in the year): "
                  f"{n_orphan} of {len(by_case)}\n")
    lines.append(f"**Low-confidence cases** (1-2 fires): {n_low}\n\n")
    if n_orphan:
        orphans = [c['id'] for c in by_case if c['n_fired'] == 0]
        lines.append(f"Orphans: {', '.join('`'+o+'`' for o in orphans)}\n\n")

    # ---------- per-month ----------
    lines.append("## Per-month rollup\n\n")
    lines.append("| Month | Dates | HIT | MISS | GAP | NULL | "
                  "Sig moves | Precision | Recall |\n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for m in by_month:
        prec = (f"{m['precision']:.0f}%"
                  if m['precision'] is not None else "n/a")
        rec  = (f"{m['recall']:.0f}%"
                  if m['recall']    is not None else "n/a")
        lines.append(f"| {m['ym']} | {m['n_dates']} | {m['hit']} | "
                      f"{m['miss']} | {m['gap']} | {m['null']} | "
                      f"{m['sig']} | {prec} | {rec} |\n")
    lines.append("\n")

    # ---------- P&L sim ----------
    lines.append("## 'Follow the playbook' P&L vs buy-and-hold\n\n")
    if not pnl or not pnl.get("n_windows"):
        lines.append("_No weekly P&L windows scored (need Friday samples with "
                      "valid fwd_5d returns)._\n\n")
    else:
        lines.append("Strategy: default 100% long the universe; go to **cash** "
                      "for the next 5 trading days when any case with expected "
                      "direction DOWN or FLAT fires on the Friday close. "
                      "Sampled on consecutive Fridays so windows do not "
                      "overlap.\n\n")
        lines.append("| Metric | Value |\n|---|---|\n")
        lines.append(f"| Weekly windows | {pnl['n_windows']} |\n")
        lines.append(f"| **Buy-and-hold cumulative return** | "
                      f"{pnl['bh_cum_pct']:+.1f}% |\n")
        lines.append(f"| **Playbook strategy cumulative return** | "
                      f"{pnl['system_cum_pct']:+.1f}% |\n")
        lines.append(f"| **Alpha (system - BH)** | "
                      f"{pnl['alpha_pct']:+.1f}% |\n")
        lines.append(f"| Time invested (% of weeks long) | "
                      f"{pnl['pct_time_long']:.1f}% |\n")
        lines.append(f"| Cash weeks (defensive) | "
                      f"{pnl.get('n_cash_weeks', 0)} |\n")
        lines.append(f"| Cash weeks where market actually fell | "
                      f"{pnl.get('n_avoided_drawdown_weeks', 0)} |\n")
        lines.append(f"| System weekly hit-rate (>0 return) | "
                      f"{pnl['system_hit_rate_pct']:.1f}% |\n")
        lines.append("\n")
        lines.append("_Caveat: this is one path on one strategy on one slice. "
                      "It is illustrative, not a significance test. Drawdown-"
                      "avoidance count is the most decision-relevant number._\n\n")

    # ---------- predictions log ----------
    lines.append("## LLM predictions log (per-symbol 5-day forecasts)\n\n")
    if preds is None:
        lines.append("_`data/predictions_log.json` not present or unparseable._\n\n")
    elif preds.get("n_scored", 0) == 0:
        lines.append(f"`data/predictions_log.json` has {preds['n_predictions']} "
                      "predictions but none have been scored yet (run "
                      "`python scripts/check_predictions.py`).\n\n")
    else:
        lines.append("Direct comparison of LLM-generated 5-day predictions vs "
                      "realised 5-day returns. Outcomes are produced by "
                      "`scripts/check_predictions.py` and stored alongside "
                      "the predictions.\n\n")
        lines.append("| Metric | Value |\n|---|---|\n")
        lines.append(f"| Predictions in log | {preds['n_predictions']} |\n")
        lines.append(f"| Predictions scored | {preds['n_scored']} |\n")
        lines.append(f"| Direction hit-rate | "
                      f"{preds['direction_hit_pct']:.1f}% |\n")
        lines.append(f"| Mean absolute error vs mid forecast (pp) | "
                      f"{preds['mean_abs_error_pct']:.2f} |\n")
        lines.append("\n")
        lines.append("**By predicted direction:**\n\n")
        lines.append("| Direction | n | scored | hit % | mean actual | mean |err| |\n")
        lines.append("|---|---:|---:|---:|---:|---:|\n")
        for d in sorted(preds["by_direction"]):
            v = preds["by_direction"][d]
            lines.append(f"| {d} | {v['n']} | {v['scored']} | "
                          f"{(v['hit_pct'] or 0):.1f}% | "
                          f"{(v.get('mean_actual_pct') or 0):+.2f}% | "
                          f"{(v.get('mean_abs_err_pct') or 0):.2f}pp |\n")
        lines.append("\n")
        if preds.get("by_model"):
            lines.append("**By model:**\n\n")
            lines.append("| Model | n | scored | hit % |\n|---|---:|---:|---:|\n")
            for m in sorted(preds["by_model"]):
                v = preds["by_model"][m]
                lines.append(f"| `{m}` | {v['n']} | {v['scored']} | "
                              f"{(v['hit_pct'] or 0):.1f}% |\n")
            lines.append("\n")

    # ---------- per-date detail ----------
    lines.append("## Per-date breakdown\n\n")
    lines.append("| Date | Verdict | Fwd 5d | Fwd 21d | Cases fired |\n")
    lines.append("|---|---|---:|---:|---|\n")
    for r in rows:
        if r.get("verdict") == "ERR":
            lines.append(f"| {r['as_of']} | ERR | n/a | n/a | "
                          f"`{r.get('error','?')}` |\n")
            continue
        f5  = (f"{r['fwd_5d']*100:+.1f}%"
                if isinstance(r.get("fwd_5d"),  (int, float)) else "n/a")
        f21 = (f"{r['fwd_21d']*100:+.1f}%"
                if isinstance(r.get("fwd_21d"), (int, float)) else "n/a")
        outs = ((r.get("modes") or {}).get("with_mf_macro", {})
                  .get("case_outcomes") or [])
        casetag = ", ".join(o["id"] for o in outs) or "_(none)_"
        lines.append(f"| {r['as_of']} | **{r['verdict']}** | {f5} | {f21} | "
                      f"{casetag} |\n")
    lines.append("\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# 8) Main
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-05-01",
                     help="Start date YYYY-MM-DD (default 2025-05-01).")
    ap.add_argument("--end", default="2026-04-01",
                     help="End date YYYY-MM-DD (default 2026-04-01).")
    ap.add_argument("--out", default=None,
                     help="Override output markdown path.")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)

    out_path = Path(args.out) if args.out else OUT_PATH
    json_out = out_path.with_suffix(".json")

    rows  = run_year(start, end)
    headline = aggregate(rows)
    by_case  = per_case_stats(rows)
    by_month = per_month_stats(rows)
    preds    = predictions_summary()
    pnl      = simulate_strategy_pnl(rows, start, end)

    md = render(rows, headline, by_case, by_month, preds, pnl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    json_out.write_text(json.dumps({
        "headline": headline, "by_case": by_case,
        "by_month": by_month, "predictions": preds,
        "pnl": pnl,
    }, indent=2, default=str), encoding="utf-8")

    print()
    print(f"Headline ({headline['n_dates']} dates, {headline['n_significant']} significant moves):")
    print(f"  HIT       : {headline['hit']:>3}")
    print(f"  MISS      : {headline['miss']:>3}")
    print(f"  GAP       : {headline['gap']:>3}")
    print(f"  NULL      : {headline['null']:>3}")
    if headline['precision'] is not None:
        print(f"  Precision : {headline['precision']:.1f}%")
    if headline['recall'] is not None:
        print(f"  Recall on sig moves : {headline['recall']:.1f}%")
    print(f"  Match coverage      : {headline['match_coverage_pct']:.1f}%")
    print()
    print("Top 5 most-fired cases:")
    for c in by_case[:5]:
        hr = (f"{c['hit_rate']:.0f}%" if c['hit_rate'] is not None else "n/a")
        m21 = (f"{(c['mean_fwd_21d_pct'] or 0):+.1f}%"
                if c['mean_fwd_21d_pct'] is not None else "n/a")
        print(f"  {c['n_fired']:>3}x  {c['id']:<35}  hit={hr:<5}  mean_21d={m21}")
    n_orphan = sum(1 for c in by_case if c['n_fired'] == 0)
    print(f"\nOrphan cases (never fired): {n_orphan} of {len(by_case)}")
    print()
    print("P&L (weekly non-overlapping windows):")
    if pnl.get("n_windows"):
        print(f"  Windows               : {pnl['n_windows']}")
        print(f"  Buy-and-hold cum ret  : {pnl['bh_cum_pct']:+.1f}%")
        print(f"  Playbook cum ret      : {pnl['system_cum_pct']:+.1f}%")
        print(f"  Alpha vs BH           : {pnl['alpha_pct']:+.1f}%")
        print(f"  Time invested         : {pnl['pct_time_long']:.0f}%")
        print(f"  System hit rate (>0)  : {pnl['system_hit_rate_pct']:.1f}%")
    else:
        print("  (no P&L windows scored)")
    print(f"\nReport saved : {out_path}")
    print(f"JSON saved   : {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
