"""Layer 3 of the 24-month free backtest: Cursor Claude (me) on 15 hand-picked
critical dates.

Two modes:

* ``--write-briefings`` (default if --score not set) -- replays the briefing
  for each critical date, runs the production briefing-compression pass, and
  writes one ``briefing.json`` + ``summary.md`` per date under
  ``data/_health/critical_dates/<YYYY-MM-DD>/``. The summary.md is what
  Cursor Claude reads in chat to make a decision.
* ``--score`` -- reads ``decisions.json`` (a single file at
  ``data/_health/critical_dates/decisions.json``, written by the chat
  walkthrough), scores each decision against forward returns, and writes
  ``data/_health/backtest_critical_dates.md`` + ``.json``.

Critical dates (15 turning points across the 24-month window):

  2024-06-10, 2024-07-29, 2024-09-12, 2024-12-16,
  2025-02-26, 2025-05-09, 2025-06-23, 2025-09-29,
  2025-10-22, 2025-11-24, 2025-12-31,
  2026-02-09, 2026-02-25, 2026-03-09, 2026-04-29

Usage::

    python scripts/backtest_critical_dates_chat.py --write-briefings
    # ... I read each summary.md in chat and write decisions.json ...
    python scripts/backtest_critical_dates_chat.py --score
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import master_strategist as ms
from brain import playbook as pb
from scripts.historical_test_playbook import (
    CASE_EXPECTED_DIRECTION,
    _replay_active_events,
)
from scripts.replay_briefing import forward_universe_return, replay_briefing

OUT_DIR = ROOT / "data" / "_health" / "critical_dates"
OUT_REPORT = ROOT / "data" / "_health" / "backtest_critical_dates.md"
OUT_JSON   = ROOT / "data" / "_health" / "backtest_critical_dates.json"
DECISIONS_FILE = OUT_DIR / "decisions.json"

CRITICAL_DATES: list[tuple[str, str]] = [
    ("2024-06-10", "First SBP rate cut of the cycle (22.0% -> 20.5%)."),
    ("2024-07-29", "IMF SBA staff-level agreement reached."),
    ("2024-09-12", "Second rate cut, KSE-100 nears 80,000."),
    ("2024-12-16", "KSE-100 record highs, MF inflows surging."),
    ("2025-02-26", "Sudden -8% drawdown on PIA / IMF concerns."),
    ("2025-05-09", "Recovery rally start after spring dip."),
    ("2025-06-23", "Brent spike + Iran-Israel ceasefire."),
    ("2025-09-29", "IMF EFF 2nd review (the case that fooled us in 1y test)."),
    ("2025-10-22", "Sharp -6% drop."),
    ("2025-11-24", "MSCI rebalance day (14 PSX adds)."),
    ("2025-12-31", "Circular debt resolution PKR 1.225tn announced."),
    ("2026-02-09", "Start of -15% drawdown."),
    ("2026-02-25", "Capitulation low."),
    ("2026-03-09", "V-recovery start."),
    ("2026-04-29", "Most recent (sanity check)."),
]


# ---------------------------------------------------------------------------
# 1) Per-stock forward return
# ---------------------------------------------------------------------------
def _forward_symbol_return(symbol: str, as_of: date,
                            days: int) -> float | None:
    if not symbol:
        return None
    try:
        import pandas as pd
        path = ROOT / "data" / "ohlcv" / f"{symbol}.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if "date" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").reset_index(drop=True)
        df = df[df["date"] >= as_of]
        if len(df) < days + 1:
            return None
        p0 = float(df.iloc[0]["close"])
        p1 = float(df.iloc[days]["close"])
        return (p1 / p0 - 1.0) if p0 > 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2) Build a focused "summary.md" of the briefing for chat reading
# ---------------------------------------------------------------------------
def _summarise_briefing(briefing: dict, as_of: str, headline: str) -> str:
    lines: list[str] = []
    lines.append(f"# Critical-date briefing: {as_of}\n")
    lines.append(f"_{headline}_\n\n")
    lines.append("**Your task:** read the structured signals below and "
                  "decide what the Master Strategist would say *as of close "
                  "of business* on this date. Output JSON of the form:\n\n")
    lines.append("```json\n{\n  \"date\": \"YYYY-MM-DD\",\n"
                  "  \"action\": \"BUY|SELL|HOLD|REDUCE|CASH\",\n"
                  "  \"conviction\": \"HIGH|MEDIUM|LOW\",\n"
                  "  \"top_buy\": \"SYMBOL or null\",\n"
                  "  \"top_short\": \"SYMBOL or null\",\n"
                  "  \"thesis\": \"2-3 sentences citing the key signals\",\n"
                  "  \"contributing_signals\": [\"signal-1\", \"signal-2\", "
                  "\"signal-3\"]\n}\n```\n\n")

    # Regime + macro snapshot
    reg = briefing.get("regime") or {}
    def _pct(v):
        if isinstance(v, (int, float)):
            return f"{v*100:+.2f}%"
        return f"`{v}`" if v is not None else "n/a"
    lines.append("## Regime\n\n")
    lines.append(f"- Regime: **{reg.get('regime', 'unknown')}**\n")
    lines.append(f"- Universe lookback 5d ret: {_pct(reg.get('universe_ret_5d'))}, "
                  f"21d: {_pct(reg.get('universe_ret_21d'))}\n")
    bp = reg.get("breadth_pct_up")
    bp_s = f"{bp:.1f}%" if isinstance(bp, (int, float)) else "n/a"
    lines.append(f"- Breadth (% advancing): `{bp_s}`\n")
    lines.append(f"- Exposure multiplier: `{reg.get('exposure_multiplier', 'n/a')}`\n\n")

    sig = briefing.get("strategy_signal") or {}
    lines.append("## Phase-1 strategy signal\n\n")
    lines.append(f"- market_risk_on: `{sig.get('market_risk_on', 'n/a')}`\n")
    selected = sig.get("selected") or []
    if selected:
        lines.append(f"- Selected (today's picks): "
                      f"{', '.join(f'`{s}`' for s in selected[:10])}\n")
    else:
        lines.append("- Selected: _(none -- Phase-1 has no entry today)_\n")
    lines.append("\n")

    pol = briefing.get("policy_rate") or {}
    lines.append(f"## Policy rate\n\n")
    lines.append(f"- SBP policy rate: **{pol.get('policy_rate_pct', 'n/a')}%**\n")
    lines.append(f"- Cycle phase: `{pol.get('cycle_phase', 'n/a')}`, "
                  f"days since last decision: `{pol.get('days_since_last', 'n/a')}`\n\n")

    kpis = ((briefing.get("industry_kpis") or {}).get("kpis") or {})
    lines.append("## Macro KPIs\n\n")
    for k in ("kibor_3m_pct", "tbill_3m_pct", "cpi_yoy_pct",
              "reserves_total_usd_mn", "kse100_ret_5d", "kse100_ret_21d"):
        lines.append(f"- `{k}`: `{kpis.get(k, 'n/a')}`\n")
    lines.append("\n")

    # Active events / drivers
    events = briefing.get("_replay_events") or []
    lines.append("## Active events\n\n")
    if not events:
        lines.append("- _(none)_\n")
    for e in events:
        lines.append(f"- `{e.get('key')}` (`{e.get('type', '')}`)\n")
    lines.append("\n")

    drivers = ((briefing.get("macro_impact") or {}).get("drivers") or [])
    lines.append("## Macro drivers\n\n")
    if not drivers:
        lines.append("- _(none)_\n")
    for d in drivers[:10]:
        lines.append(f"- `{d.get('tag')}` ({d.get('magnitude', '?')})\n")
    lines.append("\n")

    # Mutual-fund flows
    mf = briefing.get("mf_holdings") or {}
    if mf:
        lines.append("## Mutual-fund flows (last 30d / 180d)\n\n")
        lines.append(f"- Universe net flow PKR mn (30d): "
                      f"`{mf.get('universe_net_flow_30d_pkr_mn', 'n/a')}`\n")
        lines.append(f"- Universe net flow PKR mn (180d): "
                      f"`{mf.get('universe_net_flow_180d_pkr_mn', 'n/a')}`\n")
        lines.append(f"- Data freshness: `{mf.get('data_freshness_days', 'n/a')}d`\n")
        top_acc = (mf.get("top_accumulated_180d") or [])[:5]
        top_dis = (mf.get("top_distributed_180d") or [])[:5]
        def _sym_list(items):
            return ", ".join("`" + str(x.get("symbol", "?")) + "`"
                              for x in items)
        if top_acc:
            lines.append(f"- Top accumulated 180d: {_sym_list(top_acc)}\n")
        if top_dis:
            lines.append(f"- Top distributed 180d: {_sym_list(top_dis)}\n")
        lines.append("\n")

    # MUFAP
    mufap = briefing.get("mufap_industry_summary") or {}
    if mufap and not mufap.get("error"):
        lines.append("## MUFAP industry AUMs\n\n")
        lines.append(f"- Equity AUM (PKR bn): "
                      f"`{mufap.get('equity_aum_pkr_bn', 'n/a')}`\n")
        lines.append(f"- 1m delta: `{mufap.get('equity_aum_1m_delta_pct', 'n/a')}%`, "
                      f"3m delta: `{mufap.get('equity_aum_3m_delta_pct', 'n/a')}%`\n")
        lines.append(f"- Z-score 12m: `{mufap.get('equity_aum_z_12m', 'n/a')}`\n\n")

    # PSX turnover, remittances, LSM, MSCI
    for key, label in (
        ("psx_turnover", "PSX turnover"),
        ("remittances", "Remittances (SBP)"),
        ("lsm_index", "LSM index (PBS)"),
        ("msci_calendar", "MSCI calendar"),
    ):
        v = briefing.get(key)
        if isinstance(v, dict) and not v.get("error"):
            lines.append(f"## {label}\n\n")
            for kk, vv in list(v.items())[:8]:
                lines.append(f"- `{kk}`: `{vv}`\n")
            lines.append("\n")

    # FIPI flows
    fipi = briefing.get("fipi_flows") or {}
    if fipi and not fipi.get("error"):
        lines.append("## FIPI flows\n\n")
        for k, v in list(fipi.items())[:6]:
            lines.append(f"- `{k}`: `{v}`\n")
        lines.append("\n")

    # Verdict universe (already compressed)
    vu = briefing.get("verdict_universe") or {}
    if vu:
        lines.append("## 7-lens verdicts (top-K detail + summary)\n\n")
        if vu.get("_summary"):
            s = vu["_summary"]
            lines.append(f"- Summary across {s.get('n_total', '?')} symbols: "
                          f"`{s.get('action_distribution', {})}`\n")
        for sym, v in vu.items():
            if sym.startswith("_"):
                continue
            if not isinstance(v, dict):
                continue
            act = v.get("action", "?")
            conv = v.get("conviction", "?")
            sc = v.get("score", "?")
            lines.append(f"  - `{sym}` -> {act} {conv} (score {sc})\n")
        lines.append("\n")

    # Predictions (LLM 5d forecasts) -- compressed
    preds = briefing.get("predictions") or {}
    if preds:
        lines.append("## LLM 5d predictions (top-K detail)\n\n")
        for sym, p in preds.items():
            if sym.startswith("_"):
                continue
            if not isinstance(p, dict):
                continue
            mid = p.get("mid", p.get("mid_pct", "?"))
            lines.append(f"  - `{sym}`: mid `{mid}` "
                          f"low `{p.get('low', p.get('low_pct', '?'))}` "
                          f"high `{p.get('high', p.get('high_pct', '?'))}`\n")
        lines.append("\n")

    # Pre-computed playbook analogues (this is critical context)
    analogues = briefing.get("playbook_analogues") or []
    lines.append("## Pre-computed playbook analogues (from rules engine)\n\n")
    if not analogues:
        lines.append("- _(no cases fired -- this is informational, you can "
                      "still take a stance from raw signals)_\n")
    for a in analogues[:6]:
        lines.append(f"- **{a.get('id')}** "
                      f"(score {a.get('match_score', '?')}): "
                      f"fired triggers `{a.get('fired_triggers', [])}`\n")
    lines.append("\n")

    # News / sentiment
    news = briefing.get("scored_sentiment") or {}
    if news and not news.get("error"):
        lines.append("## News sentiment\n\n")
        for k, v in list(news.items())[:6]:
            lines.append(f"- `{k}`: `{v}`\n")
        lines.append("\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# 3) Write briefings
# ---------------------------------------------------------------------------
def write_briefings() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pb._load_active_events = _replay_active_events  # noqa: SLF001
    from scripts import historical_test_playbook as hist

    written = []
    for as_of_str, headline in CRITICAL_DATES:
        as_of = date.fromisoformat(as_of_str)
        hist._REPLAY_AS_OF = as_of  # noqa: SLF001
        try:
            briefing = replay_briefing(as_of)
            try:
                briefing = ms._compress_heavy_fields(briefing)  # noqa: SLF001
            except Exception as e:
                briefing["_compression_error"] = f"{type(e).__name__}: {e}"

            d_dir = OUT_DIR / as_of_str
            d_dir.mkdir(parents=True, exist_ok=True)
            (d_dir / "briefing.json").write_text(
                json.dumps(briefing, indent=2, default=str), encoding="utf-8")
            (d_dir / "summary.md").write_text(
                _summarise_briefing(briefing, as_of_str, headline),
                encoding="utf-8")

            kb = round((d_dir / "briefing.json").stat().st_size / 1024, 1)
            print(f"  {as_of_str}  -> briefing.json ({kb} KB) + summary.md")
            written.append({"date": as_of_str, "headline": headline,
                              "briefing_kb": kb})
        except Exception as e:
            print(f"  {as_of_str}  ERR  {type(e).__name__}: {e}")
            written.append({"date": as_of_str, "headline": headline,
                              "error": f"{type(e).__name__}: {e}"})

    (OUT_DIR / "INDEX.json").write_text(
        json.dumps(written, indent=2), encoding="utf-8")
    print(f"\nWrote {len(written)} briefings under {OUT_DIR}/")
    print(f"Index: {OUT_DIR / 'INDEX.json'}")
    print(f"\nNext: read each summary.md in chat, build {DECISIONS_FILE}, "
          f"then run with --score.")


# ---------------------------------------------------------------------------
# 4) Score decisions
# ---------------------------------------------------------------------------
def score_decisions() -> int:
    if not DECISIONS_FILE.exists():
        print(f"[error] {DECISIONS_FILE} not found.")
        print("Write decisions in this format and re-run with --score:")
        print('[{"date":"2024-06-10","action":"BUY","conviction":"HIGH",'
              '"top_buy":"MEBL","top_short":null,'
              '"thesis":"...","contributing_signals":["..."]},...]')
        return 2

    try:
        decisions = json.loads(DECISIONS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[error] decisions.json parse error: {e}")
        return 2

    pb._load_active_events = _replay_active_events  # noqa: SLF001
    from scripts import historical_test_playbook as hist

    rows: list[dict] = []
    for dec in decisions:
        as_of_str = dec.get("date")
        if not as_of_str:
            continue
        as_of = date.fromisoformat(as_of_str)
        hist._REPLAY_AS_OF = as_of  # noqa: SLF001

        action = (dec.get("action") or "").upper()
        top_buy = dec.get("top_buy")
        top_short = dec.get("top_short")
        conviction = dec.get("conviction")

        fwd5  = forward_universe_return(as_of, 5)
        fwd21 = forward_universe_return(as_of, 21)

        buy5  = _forward_symbol_return(top_buy, as_of, 5)  if top_buy  else None
        buy21 = _forward_symbol_return(top_buy, as_of, 21) if top_buy  else None
        sh5   = _forward_symbol_return(top_short, as_of, 5)  if top_short else None
        sh21  = _forward_symbol_return(top_short, as_of, 21) if top_short else None

        # Direction scoring
        direction_hit = None
        if isinstance(fwd5, (int, float)):
            if action in ("BUY", "LONG", "BULLISH", "ACCUMULATE", "ADD"):
                direction_hit = (fwd5 > 0)
            elif action in ("SELL", "SHORT", "BEARISH", "REDUCE", "CASH",
                              "DISTRIBUTE", "TRIM"):
                direction_hit = (fwd5 < 0)
            elif action in ("HOLD", "NEUTRAL", "WAIT"):
                direction_hit = (abs(fwd5) < 0.02)

        # Pick scoring (top-buy should rise faster than the universe)
        pick_alpha_5d = None
        if isinstance(buy5, (int, float)) and isinstance(fwd5, (int, float)):
            pick_alpha_5d = buy5 - fwd5

        rows.append({
            "date": as_of_str,
            "action": action,
            "conviction": conviction,
            "top_buy":   top_buy,
            "top_short": top_short,
            "fwd_universe_5d_pct":  (None if fwd5 is None else fwd5 * 100.0),
            "fwd_universe_21d_pct": (None if fwd21 is None else fwd21 * 100.0),
            "fwd_top_buy_5d_pct":  (None if buy5 is None else buy5 * 100.0),
            "fwd_top_buy_21d_pct": (None if buy21 is None else buy21 * 100.0),
            "fwd_top_short_5d_pct":  (None if sh5 is None else sh5 * 100.0),
            "fwd_top_short_21d_pct": (None if sh21 is None else sh21 * 100.0),
            "direction_hit": direction_hit,
            "pick_alpha_5d_pct": (None if pick_alpha_5d is None
                                  else pick_alpha_5d * 100.0),
            "thesis": dec.get("thesis"),
            "contributing_signals": dec.get("contributing_signals"),
        })

    n = len(rows)
    n_dir = sum(1 for r in rows if r["direction_hit"] is not None)
    n_hit = sum(1 for r in rows if r["direction_hit"] is True)
    alphas = [r["pick_alpha_5d_pct"] for r in rows
                if isinstance(r.get("pick_alpha_5d_pct"), (int, float))]
    mean_alpha = (sum(alphas) / len(alphas)) if alphas else None

    summary = {
        "n_decisions": n,
        "n_direction_scored": n_dir,
        "n_direction_hits": n_hit,
        "direction_hit_rate_pct": (n_hit / n_dir * 100.0 if n_dir else None),
        "mean_pick_alpha_5d_pct": mean_alpha,
        "n_picks_scored": len(alphas),
    }

    # Render markdown
    lines: list[str] = []
    lines.append("# Layer 3 -- Cursor Claude critical-date backtest\n\n")
    lines.append(f"_Run at {datetime.now().isoformat(timespec='seconds')}_\n\n")
    lines.append("Hand-picked turning points across 24 months. I (Cursor "
                  "Claude) read each replayed briefing and produced a "
                  "strategist-format JSON decision *before* seeing the forward "
                  "return. The script then scored each call.\n\n")

    lines.append("## Headline\n\n")
    lines.append("| Metric | Value |\n|---|---|\n")
    lines.append(f"| Decisions | {summary['n_decisions']} |\n")
    if summary["direction_hit_rate_pct"] is not None:
        lines.append(f"| **Direction hit-rate (5d)** | "
                      f"{summary['direction_hit_rate_pct']:.1f}% "
                      f"({summary['n_direction_hits']}/"
                      f"{summary['n_direction_scored']}) |\n")
    if summary["mean_pick_alpha_5d_pct"] is not None:
        lines.append(f"| **Mean top-pick alpha vs universe (5d)** | "
                      f"{summary['mean_pick_alpha_5d_pct']:+.2f}pp "
                      f"({summary['n_picks_scored']} scored) |\n")
    lines.append("\n")

    lines.append("## Per-date detail\n\n")
    lines.append("| Date | Action | Conv | Top buy | Buy 5d | Universe 5d | "
                  "Pick alpha 5d | Hit |\n")
    lines.append("|---|---|---|---|---:|---:|---:|---|\n")
    for r in rows:
        u5 = r.get("fwd_universe_5d_pct")
        b5 = r.get("fwd_top_buy_5d_pct")
        pa = r.get("pick_alpha_5d_pct")
        u5s = (f"{u5:+.1f}%" if isinstance(u5, (int, float)) else "n/a")
        b5s = (f"{b5:+.1f}%" if isinstance(b5, (int, float)) else "n/a")
        pas = (f"{pa:+.1f}pp" if isinstance(pa, (int, float)) else "n/a")
        hit = r.get("direction_hit")
        hit_s = ("HIT" if hit is True else
                 "MISS" if hit is False else "")
        lines.append(f"| {r['date']} | {r.get('action') or ''} | "
                      f"{r.get('conviction') or ''} | "
                      f"{r.get('top_buy') or ''} | {b5s} | {u5s} | "
                      f"{pas} | {hit_s} |\n")
    lines.append("\n")

    lines.append("## Theses (one line each)\n\n")
    for r in rows:
        th = r.get("thesis") or "_(none)_"
        lines.append(f"* **{r['date']}** ({r.get('action')}, "
                      f"{r.get('conviction')}): {th}\n")
    lines.append("\n")

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text("".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"summary": summary, "rows": rows},
                                     indent=2, default=str), encoding="utf-8")

    print("\nLayer 3 summary:")
    print(f"  Decisions             : {summary['n_decisions']}")
    if summary["direction_hit_rate_pct"] is not None:
        print(f"  Direction hit-rate    : "
              f"{summary['direction_hit_rate_pct']:.1f}%")
    if summary["mean_pick_alpha_5d_pct"] is not None:
        print(f"  Mean top-pick alpha   : "
              f"{summary['mean_pick_alpha_5d_pct']:+.2f}pp")
    print(f"\nReport : {OUT_REPORT}")
    print(f"JSON   : {OUT_JSON}")
    return 0


# ---------------------------------------------------------------------------
# 5) Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-briefings", action="store_true",
                     help="Replay + compress briefings for the 15 critical dates.")
    ap.add_argument("--score", action="store_true",
                     help="Read decisions.json and score against forward returns.")
    args = ap.parse_args()

    if args.score:
        return score_decisions()

    write_briefings()
    return 0


if __name__ == "__main__":
    sys.exit(main())
