"""Historical test of the playbook knowledge base.

Walks a list of historical dates spanning 2021-2026, replays the
briefing for each (using ``scripts.replay_briefing``), runs the
playbook matcher, and compares what the matcher fired vs what
actually happened in the next 5 / 21 trading days.

Output: a Markdown report at ``data/_health/playbook_historical_test.md``
plus a console summary. The report tags each test point as:

  * **HIT**  — at least one analogue fired AND its directional
               playbook matched the actual forward return
  * **MISS** — analogue fired but its directional playbook
               disagreed with the actual return
  * **GAP**  — significant move (|21d|>=8% OR |5d|>=4%) where NO
               analogue fired (the library has nothing for this
               situation)
  * **NULL** — boring period (no significant move) and no analogue
               fired (correct quiet)

Only HIT / MISS / GAP rows reveal anything about the matcher's
quality. NULL rows are noise.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import playbook as pb
from scripts.replay_briefing import (
    replay_briefing, forward_universe_return, forward_symbol_return,
    HISTORICAL_EVENTS,
)

OUT_PATH = ROOT / "data" / "_health" / "playbook_historical_test.md"
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1) Test dates: a mix of known-significant + boring controls + random
# ---------------------------------------------------------------------------
NAMED_TEST_DATES: list[tuple[str, str]] = [
    ("2022-03-08", "Russia-Ukraine: Brent +20%/21d, KSE -8%"),
    ("2022-04-08", "Emergency 250bp SBP hike (PKR/IMF stress)"),
    ("2022-07-12", "Brent rolling over after invasion peak"),
    ("2023-01-30", "PKR cap removal: ~10% drop in 3 days"),
    ("2023-03-06", "300bp emergency hike to 20%"),
    ("2023-06-28", "Cycle peak: 22% policy rate"),
    ("2023-07-13", "IMF $3bn SBA approved"),
    ("2023-08-15", "Post-IMF rally + FIPI inflows"),
    ("2024-02-09", "Election week (contested results)"),
    ("2024-06-11", "FIRST RATE CUT of cycle (22% -> 20.5%)"),
    ("2024-07-30", "Second cut (20.5% -> 19.5%)"),
    ("2024-09-26", "IMF $7bn EFF + 200bp cut chain"),
    ("2024-12-17", "5th consecutive cut: 200bp to 13%"),
    ("2025-01-28", "6th cut: 100bp to 12%"),
    ("2025-05-06", "8th cut: 100bp to 11% — bottom of cycle"),
    ("2025-12-15", "Rs 1.225trn circular-debt resolution"),
    # Boring controls
    ("2021-08-16", "CONTROL: nothing happening"),
    ("2024-04-15", "CONTROL: mid-cycle quiet period"),
    ("2025-09-08", "CONTROL: post-rate-cut quiet"),
]

# MF-stress dates: months when AHL-published Mutual Funds Equity
# Holdings reports flag a meaningful flow event. We can only test
# months where (a) the MF parquet has data AND (b) we have OHLCV for
# 5d/21d forward returns. The Jun-2025 report flagged 14 new top-10
# entrants vs May-2025 (smart-money initiation cluster); the Jan-2026
# report flagged sustained PSO distribution (-0.9pp MoM) and broad
# energy-sector rotation. Limited to ~5 months -- as more PDFs come
# in, expand this list.
MF_STRESS_TEST_DATES: list[tuple[str, str]] = [
    ("2025-06-30", "MF: post Jun-25 AHL pub (14 new entrants vs May-25)"),
    ("2025-07-17", "MF: Jun-25 AHL report publication day"),
    ("2025-07-21", "MF: 1 trading day after Jun-25 publication"),
    ("2025-08-04", "MF: 2 weeks after Jun-25 publication"),
    ("2026-02-15", "MF: post Jan-26 AHL pub (PSO -0.9pp dist, FFC -0.8pp)"),
    ("2026-02-19", "MF: Jan-26 AHL report publication day"),
    ("2026-02-23", "MF: 1 trading day after Jan-26 publication"),
    ("2026-03-09", "MF: 3 weeks after Jan-26 publication"),
]


def _random_dates(n: int, seed: int = 42,
                   start: date = date(2021, 6, 1),
                   end: date = date(2026, 3, 1)) -> list[tuple[str, str]]:
    """Pick n weekday dates uniformly from [start, end] for an
    unbiased recall measurement."""
    rng = random.Random(seed)
    out: list[tuple[str, str]] = []
    span = (end - start).days
    seen: set[date] = set()
    while len(out) < n:
        d = start + timedelta(days=rng.randint(0, span))
        if d.weekday() >= 5 or d in seen:
            continue
        seen.add(d)
        out.append((d.isoformat(), f"RANDOM ({d.strftime('%a')})"))
    return out


RANDOM_TEST_DATES = _random_dates(25, seed=42)


# ---------------------------------------------------------------------------
# 2) Per-case directional expectation (read from the playbook prose)
# ---------------------------------------------------------------------------
# We map each case_id to a coarse expected direction over 21 trading
# days. Hand-coded from the case's ``playbook`` paragraph. This lets
# us automate hit/miss scoring without the LLM in the loop.
CASE_EXPECTED_DIRECTION: dict[str, str] = {
    # Bullish cases
    "circular_debt_resolution_large":  "UP",
    "sbp_rate_cut_cycle_initiation":   "UP",
    "imf_sba_eff_approval":            "UP",
    "imf_review_completed":            "UP",
    "post_cut_cycle_continuation":     "UP",
    "phase1_cash_in_uptrend":          "UP",
    "behavioural_panic_3day":          "UP",   # mean-revert
    "fipi_capitulation":               "UP",   # contrarian
    "brent_spike_e_and_p":             "UP",   # E&P specifically
    # Bearish cases
    "circular_debt_worsening_large":   "DOWN",
    "sbp_rate_hike_shock":             "DOWN",
    "nth_rate_cut_profit_taking":      "DOWN",
    "pkr_devaluation_shock":           "MIXED",  # E&P up / Cement down
    "cement_coal_shock":               "DOWN",   # cement specifically
    "election_window_chop":            "FLAT",
    # Risk-management
    "earnings_blackout_concentration": "FLAT",
    # Phase-D MF flow cases
    "mf_accumulation_strong":          "UP",
    "mf_distribution_strong":          "DOWN",
    "mf_initiation_cluster":           "UP",
    "mf_capitulation_with_value":      "UP",   # contrarian
    "mf_smart_money_divergence":       "UP",
    "mf_universe_distribution_broad":  "DOWN",
    # Phase-E volume + Tier-1 add-ons (added 2026-05-03 — these
    # cases were defaulting to FLAT scoring which understated the
    # case's directional bias). Banking NIM cases scored at the
    # universe level even though their actual bias is on the bank
    # basket — UP/DOWN is a coarse but defensible labelling.
    "volume_confirmation_breakout":    "UP",
    "banking_nim_regime_high":         "UP",
    "banking_nim_regime_low":          "DOWN",
    "rate_cycle_pivot_diagnostic":     "FLAT",
}


# ---------------------------------------------------------------------------
# 3) Patch the matcher's event loader so it sees historical events
#    instead of the live data/playbook/_events.json file.
# ---------------------------------------------------------------------------
_REPLAY_AS_OF: date | None = None


def _replay_active_events(path=None):
    if _REPLAY_AS_OF is None:
        return set()
    out: set[str] = set()
    for ev in HISTORICAL_EVENTS:
        try:
            d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        decay = int(ev.get("decay_days") or 14)
        if d <= _REPLAY_AS_OF and (_REPLAY_AS_OF - d).days <= decay:
            out.add(str(ev.get("key") or "").lower())
    return out


# ---------------------------------------------------------------------------
# 4) Score a single test point
# ---------------------------------------------------------------------------
def _classify_outcome(case_id: str, ret_5d: float | None,
                       ret_21d: float | None) -> str:
    """Decide HIT / MISS for a fired case given its expected
    direction and the actual forward returns."""
    expected = CASE_EXPECTED_DIRECTION.get(case_id, "FLAT")
    r = ret_21d if ret_21d is not None else ret_5d
    if r is None:
        return "?"
    if expected == "UP":
        return "HIT" if r > 0.005 else "MISS"
    if expected == "DOWN":
        return "HIT" if r < -0.005 else "MISS"
    if expected == "FLAT":
        return "HIT" if abs(r) < 0.04 else "MISS"
    if expected == "MIXED":
        # PKR-shock case: not directional, just acknowledges the event.
        # Score as HIT if anything material happened.
        return "HIT" if abs(r) > 0.02 else "MISS"
    return "?"


def _apply_mode(briefing: dict, mode: str) -> dict:
    """Strip MF / macro KPI fields based on the comparison mode.

    * ``baseline``     -- both stripped (only driver tags + universe + events)
    * ``with_macro``   -- macro KPIs ON, MF stripped
    * ``with_mf_macro`` -- both ON (the production setup)
    """
    if mode == "with_mf_macro":
        return briefing
    b = dict(briefing)
    if mode in ("baseline", "with_macro"):
        # Strip MF lens
        b["mf_holdings"] = {}
    if mode == "baseline":
        # Strip macro KPI levels (keep only universe-derived KSE proxy)
        kpis = (b.get("industry_kpis") or {}).get("kpis") or {}
        b["industry_kpis"] = {"kpis": {
            "kibor_3m_pct": None, "tbill_3m_pct": None,
            "cpi_yoy_pct":  None, "reserves_total_usd_mn": None,
            "kse100_ret_5d":  kpis.get("kse100_ret_5d"),
            "kse100_ret_21d": kpis.get("kse100_ret_21d"),
        }}
    return b


def run_one(label: str, as_of: date,
              modes: list[str] | None = None) -> dict:
    global _REPLAY_AS_OF
    _REPLAY_AS_OF = as_of
    pb._load_active_events = _replay_active_events  # noqa: SLF001

    briefing = replay_briefing(as_of)

    fwd_5d  = forward_universe_return(as_of, 5)
    fwd_21d = forward_universe_return(as_of, 21)
    sig_5d  = (fwd_5d  is not None and abs(fwd_5d)  >= 0.04)
    sig_21d = (fwd_21d is not None and abs(fwd_21d) >= 0.08)
    sig = sig_5d or sig_21d

    modes = modes or ["with_mf_macro"]
    mode_results: dict[str, dict] = {}

    for mode in modes:
        b = _apply_mode(briefing, mode)
        facts = pb.summarise_facts(b)
        analogues = pb.retrieve_analogues(b, top_k=4)
        case_outcomes = []
        for a in analogues:
            cls = _classify_outcome(a["id"], fwd_5d, fwd_21d)
            case_outcomes.append({"id": a["id"], "score": a["match_score"],
                                    "expected": CASE_EXPECTED_DIRECTION
                                                  .get(a["id"], "?"),
                                    "outcome": cls,
                                    "fired": a["fired_triggers"]})
        if not analogues and sig:
            verdict = "GAP"
        elif not analogues:
            verdict = "NULL"
        elif any(o["outcome"] == "HIT" for o in case_outcomes):
            verdict = "HIT"
        else:
            verdict = "MISS"
        mode_results[mode] = {
            "verdict": verdict,
            "n_analogues": len(analogues),
            "case_outcomes": case_outcomes,
            "facts_summary": {
                "ret_5d": facts.get("universe_5d"),
                "breadth": facts.get("breadth"),
                "drivers": facts.get("drivers"),
                "active_events": facts.get("active_events"),
            },
        }

    primary = mode_results.get(modes[-1]) or {}
    return {
        "label": label, "as_of": as_of.isoformat(),
        "fwd_5d": fwd_5d, "fwd_21d": fwd_21d,
        "regime": briefing["regime"]["regime"],
        "drivers": [d["tag"] for d in briefing["macro_impact"]["drivers"]],
        "events":  [e["key"] for e in briefing["_replay_events"]],
        "policy_rate": briefing["policy_rate"]["policy_rate_pct"],
        "n_universe": briefing["_replay_universe"]["n_symbols"],
        "modes": mode_results,
        # Backward-compat fields use the primary mode
        "n_analogues": primary.get("n_analogues", 0),
        "case_outcomes": primary.get("case_outcomes", []),
        "verdict": primary.get("verdict", "ERR"),
        "significant": sig,
    }


# ---------------------------------------------------------------------------
# 5) Main
# ---------------------------------------------------------------------------
MODE_LABELS = {
    "baseline":     "Baseline (no macro KPIs, no MF)",
    "with_macro":   "With macro KPIs only",
    "with_mf_macro": "With MF + macro (production)",
}


def _stats(rs: list[dict], mode: str) -> dict:
    """Aggregate verdicts for a given comparison mode across a list
    of run_one results."""
    c = Counter()
    sig = 0
    for r in rs:
        if r.get("verdict") == "ERR":
            c["ERR"] += 1
            continue
        m = (r.get("modes") or {}).get(mode) or {}
        v = m.get("verdict", "ERR")
        c[v] += 1
        if r.get("significant"):
            sig += 1
    prec = (c["HIT"] / (c["HIT"] + c["MISS"]) * 100
            if (c["HIT"] + c["MISS"]) > 0 else None)
    rec  = ((sig - c["GAP"]) / sig * 100) if sig > 0 else None
    return {"hit": c["HIT"], "miss": c["MISS"], "gap": c["GAP"],
            "null": c["NULL"], "err": c.get("ERR", 0),
            "sig": sig, "precision": prec, "recall": rec}


def main() -> int:
    results: list[dict] = []
    all_dates = NAMED_TEST_DATES + MF_STRESS_TEST_DATES + RANDOM_TEST_DATES
    print(f"\nRunning historical playbook test on {len(all_dates)} dates "
          f"({len(NAMED_TEST_DATES)} named + "
          f"{len(MF_STRESS_TEST_DATES)} MF-stress + "
          f"{len(RANDOM_TEST_DATES)} random)...")
    for dstr, label in all_dates:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        try:
            r = run_one(label, d,
                         modes=["baseline", "with_macro", "with_mf_macro"])
        except Exception as e:
            r = {"label": label, "as_of": dstr, "verdict": "ERR",
                 "error": f"{type(e).__name__}: {e}"}
        results.append(r)
        verdict = r.get("verdict", "?")
        fwd5  = r.get("fwd_5d")
        fwd21 = r.get("fwd_21d")
        f5  = f"{fwd5*100:+5.1f}%" if isinstance(fwd5, (int, float)) else "  n/a"
        f21 = f"{fwd21*100:+5.1f}%" if isinstance(fwd21, (int, float)) else "  n/a"
        n_cases = r.get("n_analogues", 0)
        # 3-mode quick-look "B/Mc/Mf" verdicts in the console
        modes = r.get("modes") or {}
        q = "/".join(
            (modes.get(m) or {}).get("verdict", "-")[0:1] or "-"
            for m in ("baseline", "with_macro", "with_mf_macro"))
        print(f"  {dstr}  [{q}]  fwd5={f5}  fwd21={f21}  "
              f"cases(prod)={n_cases}  — {label}")

    n_named  = len(NAMED_TEST_DATES)
    n_mf     = len(MF_STRESS_TEST_DATES)
    named_results  = results[:n_named]
    mf_results     = results[n_named:n_named + n_mf]
    random_results = results[n_named + n_mf:]

    sectioned = [("Named (curated)",  named_results),
                  ("MF-stress",         mf_results),
                  ("Random (unbiased)", random_results),
                  ("**Combined**",      results)]

    print()
    print(f"{'Bucket':<22}  {'mode':<26}  HIT  MISS  GAP  NULL   "
          f"Prec    Recall")
    for name, rs in sectioned:
        for mode in ("baseline", "with_macro", "with_mf_macro"):
            s = _stats(rs, mode)
            prec = f"{s['precision']:>5.1f}%" if s['precision'] is not None else "  n/a"
            rec  = f"{s['recall']:>5.1f}%"    if s['recall']    is not None else "  n/a"
            print(f"  {name[:20]:<20}  {MODE_LABELS[mode]:<26}  "
                  f"{s['hit']:>3}  {s['miss']:>4}  {s['gap']:>3}  "
                  f"{s['null']:>4}   {prec}  {rec}")

    # ---------- Markdown report ----------
    lines = ["# Historical playbook test\n",
             f"_As of {datetime.now().isoformat(timespec='seconds')}_\n\n",
             "Three matcher configurations are compared on every test "
             "date so the lift from each new data layer is measurable:\n\n",
             "1. **Baseline** — only universe-derived signals, the macro "
             "KPI parquets are masked, MF parquets are masked.\n",
             "2. **With macro** — macro KPI levels (KIBOR, T-bills, CPI, "
             "FX-reserves) flow into the matcher; MF still off.\n",
             "3. **With MF + macro** — production setup; both layers on.\n\n"]
    for name, rs in sectioned:
        lines.append(f"## {name}\n\n")
        lines.append("| Mode | HIT | MISS | GAP | NULL | Precision | "
                      "Recall on sig moves |\n")
        lines.append("|---|---|---|---|---|---|---|\n")
        for mode in ("baseline", "with_macro", "with_mf_macro"):
            s = _stats(rs, mode)
            prec = f"{s['precision']:.1f}%" if s['precision'] is not None else "n/a"
            rec  = (f"{s['recall']:.1f}% ({s['sig'] - s['gap']}/{s['sig']})"
                     if s['recall'] is not None else "n/a")
            lines.append(f"| {MODE_LABELS[mode]} | {s['hit']} | {s['miss']} | "
                          f"{s['gap']} | {s['null']} | {prec} | {rec} |\n")
        lines.append("\n")

    lines.append("## Per-date breakdown (production mode)\n\n")
    lines.append("| Date | Label | Fwd 5d | Fwd 21d | "
                  "Baseline / +Macro / +MF | Cases fired (production) |\n")
    lines.append("|---|---|---|---|---|---|\n")
    for r in results:
        f5  = (f"{r['fwd_5d']*100:+.1f}%"  if isinstance(r.get("fwd_5d"),  (int, float)) else "n/a")
        f21 = (f"{r['fwd_21d']*100:+.1f}%" if isinstance(r.get("fwd_21d"), (int, float)) else "n/a")
        modes = r.get("modes") or {}
        triple = " / ".join((modes.get(m) or {}).get("verdict", "-")
                              for m in ("baseline", "with_macro", "with_mf_macro"))
        cases = ", ".join(o["id"] for o in
                            ((modes.get("with_mf_macro") or {}).get("case_outcomes") or [])) or "_(none)_"
        lines.append(f"| {r['as_of']} | {r['label']} | {f5} | {f21} | "
                      f"**{triple}** | {cases} |\n")

    lines.append("\n## Detailed per-date\n\n")
    for r in results:
        lines.append(f"### {r['as_of']} — {r['label']}\n\n")
        if r.get("error"):
            lines.append(f"- Error: `{r['error']}`\n\n")
            continue
        lines.append(f"- Fwd 5d: `{r.get('fwd_5d')}`, "
                      f"Fwd 21d: `{r.get('fwd_21d')}`\n")
        lines.append(f"- Regime: `{r.get('regime')}`, "
                      f"Policy rate: `{r.get('policy_rate')}`\n")
        lines.append(f"- Drivers fired: `{r.get('drivers')}`\n")
        lines.append(f"- Active events: `{r.get('events')}`\n")
        lines.append(f"- Universe size in OHLCV: `{r.get('n_universe')}`\n\n")
        for mode in ("baseline", "with_macro", "with_mf_macro"):
            m = (r.get("modes") or {}).get(mode) or {}
            outs = m.get("case_outcomes") or []
            lines.append(f"  **{MODE_LABELS[mode]}** — verdict "
                          f"`{m.get('verdict','?')}`, "
                          f"{m.get('n_analogues', 0)} analogue(s)\n")
            if outs:
                for o in outs:
                    lines.append(f"  - `{o['id']}` "
                                  f"(expected `{o['expected']}`, score "
                                  f"`{o['score']}`) -> **{o['outcome']}** "
                                  f"on triggers {o['fired']}\n")
            else:
                lines.append("  - _(no analogues matched)_\n")
            lines.append("\n")

    OUT_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"\nReport saved: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
