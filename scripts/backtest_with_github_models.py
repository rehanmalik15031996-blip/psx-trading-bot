"""Layer 2 of the 24-month free backtest: GitHub Models GPT-4o-mini brain.

Walks every Friday from --start to --end (~100 weekly samples by default),
replays the full Master Strategist briefing for that date, runs the
production briefing-compression pass, then asks **GPT-4o-mini via the free
GitHub Models tier** to produce a strategist-format JSON decision. Each
decision is scored against realised forward 5d / 21d returns.

Outputs:

* ``data/_health/backtest_github_models.json`` -- per-date raw records.
* ``data/_health/backtest_github_models.md``   -- summary table + headline
  metrics (direction hit-rate, mean fwd-5d for top_buy / top_short,
  side-by-side vs the playbook on the same date).

Cost: $0 (GitHub Copilot Free / GitHub Models free tier).
Pre-req: ``GITHUB_TOKEN`` env var with the ``models:read`` scope. If the
token is missing, the script writes a graceful "skipped" report and exits 0.

Rate-limited gracefully: GitHub Models free tier is 15 RPM / 150 RPD for
gpt-4o-mini, so the script sleeps 5 seconds between calls and stops at
~140 calls / day.

Usage::

    python scripts/backtest_with_github_models.py \
        --start 2024-05-03 --end 2026-05-01 \
        --weekday 4 --max-calls 100
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
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

OUT_JSON = ROOT / "data" / "_health" / "backtest_github_models.json"
OUT_MD   = ROOT / "data" / "_health" / "backtest_github_models.md"

# GitHub Models free tier: gpt-4o-mini = 15 RPM / 150 RPD
RATE_DELAY_S = 5.0
MAX_TOKENS_OUT = 1500


# ---------------------------------------------------------------------------
# 1) Date sample
# ---------------------------------------------------------------------------
def weekly_dates(start: date, end: date, weekday: int = 4) -> list[date]:
    out: list[date] = []
    d = start
    while d < end:
        if d.weekday() == weekday:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# 2) Per-stock forward return helper (so we can score top_buy / top_short)
# ---------------------------------------------------------------------------
def _forward_symbol_return(symbol: str, as_of: date,
                            days: int) -> float | None:
    """Forward `days`-trading-day return for `symbol` (% as decimal)."""
    if not symbol:
        return None
    try:
        import pandas as pd
        path = ROOT / "data" / "ohlcv" / f"{symbol}.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.sort_values("date").reset_index(drop=True)
        else:
            return None
        df = df[df["date"] >= as_of]
        if len(df) < days + 1:
            return None
        p0 = float(df.iloc[0]["close"])
        p1 = float(df.iloc[days]["close"])
        if p0 <= 0:
            return None
        return p1 / p0 - 1.0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3) GitHub Models call
# ---------------------------------------------------------------------------
def _make_client():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return None, "GITHUB_TOKEN env var missing (need 'models:read' scope)."
    try:
        from openai import OpenAI
    except ImportError as e:
        return None, f"openai package not installed: {e}"
    client = OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=token,
        default_headers={"X-GitHub-Api-Version": "2022-11-28"},
    )
    return client, None


def _call_strategist(client, briefing: dict,
                      model: str = "openai/gpt-4o-mini") -> dict:
    """Send briefing to GPT-4o-mini and parse the JSON decision."""
    user_msg = (
        "Today's briefing JSON follows. Output ONLY a single JSON object "
        "with the exact schema specified in your system prompt. No prose, "
        "no markdown fences, just JSON.\n\n"
        f"```json\n{json.dumps(briefing, default=str)[:120_000]}\n```"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ms.STRATEGIST_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        max_tokens=MAX_TOKENS_OUT,
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip()
    return _parse_decision(text)


def _parse_decision(text: str) -> dict:
    """Pull the first JSON object out of the LLM response."""
    if not text:
        return {"_parse_error": "empty response"}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception as e:
                return {"_parse_error": f"json: {e}",
                        "_raw_text": text[:1000]}
        return {"_parse_error": "no json object found",
                "_raw_text": text[:1000]}


# ---------------------------------------------------------------------------
# 4) Decision extraction (to support varied shapes the LLM might produce)
# ---------------------------------------------------------------------------
def _extract_action(decision: dict) -> str | None:
    if not isinstance(decision, dict):
        return None
    for key in ("action", "decision", "stance", "call"):
        v = decision.get(key)
        if isinstance(v, str) and v:
            return v.upper()
    return None


def _extract_top_buy(decision: dict) -> str | None:
    """Best-effort extraction of the single top-pick BUY symbol."""
    if not isinstance(decision, dict):
        return None
    actions = decision.get("actions")
    if isinstance(actions, list):
        for a in actions:
            if not isinstance(a, dict):
                continue
            act = (a.get("action") or "").upper()
            if act in ("BUY", "ADD", "OVERWEIGHT") and a.get("symbol"):
                return str(a["symbol"]).upper()
    for key in ("top_buy", "top_pick", "primary_buy"):
        v = decision.get(key)
        if isinstance(v, str) and v:
            return v.upper()
    return None


def _extract_top_short(decision: dict) -> str | None:
    if not isinstance(decision, dict):
        return None
    actions = decision.get("actions")
    if isinstance(actions, list):
        for a in actions:
            if not isinstance(a, dict):
                continue
            act = (a.get("action") or "").upper()
            if act in ("SELL", "TRIM", "UNDERWEIGHT", "SHORT") and a.get("symbol"):
                return str(a["symbol"]).upper()
    for key in ("top_short", "top_sell", "primary_short"):
        v = decision.get(key)
        if isinstance(v, str) and v:
            return v.upper()
    return None


def _extract_conviction(decision: dict) -> str | None:
    if not isinstance(decision, dict):
        return None
    for key in ("conviction", "confidence"):
        v = decision.get(key)
        if isinstance(v, str) and v:
            return v.upper()
    return None


# ---------------------------------------------------------------------------
# 5) Per-date pipeline
# ---------------------------------------------------------------------------
def run_one_date(client, model: str, as_of: date) -> dict:
    pb._load_active_events = _replay_active_events  # noqa: SLF001
    from scripts import historical_test_playbook as hist
    hist._REPLAY_AS_OF = as_of  # noqa: SLF001

    record: dict = {"date": as_of.isoformat()}
    try:
        briefing = replay_briefing(as_of)
    except Exception as e:
        record["error"] = f"replay_briefing: {type(e).__name__}: {e}"
        return record

    try:
        compressed = ms._compress_heavy_fields(briefing)  # noqa: SLF001
    except Exception as e:
        record["compression_error"] = f"{type(e).__name__}: {e}"
        compressed = briefing

    record["briefing_size_kb"] = round(len(json.dumps(compressed,
                                                        default=str)) / 1024, 1)

    try:
        decision = _call_strategist(client, compressed, model=model)
    except Exception as e:
        record["llm_error"] = f"{type(e).__name__}: {e}"
        return record

    record["decision_raw"] = decision
    record["action"] = _extract_action(decision)
    record["top_buy"] = _extract_top_buy(decision)
    record["top_short"] = _extract_top_short(decision)
    record["conviction"] = _extract_conviction(decision)

    record["fwd_universe_5d"]  = forward_universe_return(as_of, 5)
    record["fwd_universe_21d"] = forward_universe_return(as_of, 21)
    if record["top_buy"]:
        record["fwd_top_buy_5d"]  = _forward_symbol_return(
            record["top_buy"], as_of, 5)
        record["fwd_top_buy_21d"] = _forward_symbol_return(
            record["top_buy"], as_of, 21)
    if record["top_short"]:
        record["fwd_top_short_5d"]  = _forward_symbol_return(
            record["top_short"], as_of, 5)
        record["fwd_top_short_21d"] = _forward_symbol_return(
            record["top_short"], as_of, 21)

    # Score direction
    fwd5 = record["fwd_universe_5d"]
    if isinstance(fwd5, (int, float)) and record["action"]:
        a = record["action"]
        if a in ("BUY", "LONG", "BULLISH", "ACCUMULATE"):
            record["direction_hit"] = (fwd5 > 0)
        elif a in ("SELL", "SHORT", "BEARISH", "DISTRIBUTE", "REDUCE"):
            record["direction_hit"] = (fwd5 < 0)
        else:  # NEUTRAL / CASH / HOLD
            record["direction_hit"] = (abs(fwd5) < 0.02)
    return record


# ---------------------------------------------------------------------------
# 6) Aggregation + render
# ---------------------------------------------------------------------------
def _aggregate(records: list[dict]) -> dict:
    n = len(records)
    valid = [r for r in records if "decision_raw" in r and "error" not in r]
    n_valid = len(valid)

    n_with_dir = sum(1 for r in valid if "direction_hit" in r)
    n_dir_hit  = sum(1 for r in valid if r.get("direction_hit") is True)

    actions = {}
    for r in valid:
        a = r.get("action") or "_none_"
        actions[a] = actions.get(a, 0) + 1

    buy_fwd5  = [r["fwd_top_buy_5d"]  for r in valid
                  if isinstance(r.get("fwd_top_buy_5d"),  (int, float))]
    short_fwd5 = [r["fwd_top_short_5d"] for r in valid
                  if isinstance(r.get("fwd_top_short_5d"), (int, float))]

    return {
        "n_dates": n,
        "n_valid": n_valid,
        "n_errors": n - n_valid,
        "n_with_direction_score": n_with_dir,
        "n_direction_hits": n_dir_hit,
        "direction_hit_rate_pct": (n_dir_hit / n_with_dir * 100.0
                                    if n_with_dir else None),
        "action_distribution": actions,
        "mean_top_buy_fwd_5d_pct":   (sum(buy_fwd5)  / len(buy_fwd5)  * 100.0
                                       if buy_fwd5  else None),
        "mean_top_short_fwd_5d_pct": (sum(short_fwd5) / len(short_fwd5) * 100.0
                                       if short_fwd5 else None),
        "n_top_buy_scored":   len(buy_fwd5),
        "n_top_short_scored": len(short_fwd5),
    }


def _render(records: list[dict], summary: dict, model: str,
             start: date, end: date, weekday: int) -> str:
    lines: list[str] = []
    lines.append(f"# Layer 2 -- GPT-4o-mini brain backtest\n\n")
    lines.append(f"_Run at {datetime.now().isoformat(timespec='seconds')}_\n\n")
    lines.append(f"Model: `{model}` via GitHub Models (free tier).\n")
    lines.append(f"Sample: {summary['n_dates']} weekly dates "
                  f"({start} -> {end}, weekday={weekday}).\n\n")

    lines.append("## Headline\n\n")
    lines.append("| Metric | Value |\n|---|---|\n")
    lines.append(f"| Dates evaluated | {summary['n_dates']} |\n")
    lines.append(f"| Valid LLM decisions | {summary['n_valid']} |\n")
    lines.append(f"| Errors (replay or LLM) | {summary['n_errors']} |\n")
    if summary.get("direction_hit_rate_pct") is not None:
        lines.append(f"| **Direction hit-rate (5d)** | "
                      f"{summary['direction_hit_rate_pct']:.1f}% "
                      f"({summary['n_direction_hits']}/"
                      f"{summary['n_with_direction_score']}) |\n")
    if summary.get("mean_top_buy_fwd_5d_pct") is not None:
        lines.append(f"| Mean fwd-5d on top_buy ({summary['n_top_buy_scored']} "
                      f"scored) | {summary['mean_top_buy_fwd_5d_pct']:+.2f}% |\n")
    if summary.get("mean_top_short_fwd_5d_pct") is not None:
        lines.append(f"| Mean fwd-5d on top_short "
                      f"({summary['n_top_short_scored']} scored) | "
                      f"{summary['mean_top_short_fwd_5d_pct']:+.2f}% |\n")
    lines.append("\n")

    lines.append("## Action distribution\n\n")
    lines.append("| Action | Count |\n|---|---:|\n")
    for k, v in sorted(summary["action_distribution"].items(),
                        key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {v} |\n")
    lines.append("\n")

    lines.append("## Per-date detail\n\n")
    lines.append("| Date | Action | Conv | Top buy | Buy 5d | Top short | "
                  "Short 5d | Universe 5d | Hit |\n")
    lines.append("|---|---|---|---|---:|---|---:|---:|---|\n")
    for r in records:
        if "error" in r:
            lines.append(f"| {r['date']} | ERR | | | | | | | "
                          f"`{r['error'][:60]}` |\n")
            continue
        u5 = r.get("fwd_universe_5d")
        u5s = (f"{u5*100:+.1f}%" if isinstance(u5, (int, float)) else "n/a")
        b5 = r.get("fwd_top_buy_5d")
        b5s = (f"{b5*100:+.1f}%" if isinstance(b5, (int, float)) else "n/a")
        s5 = r.get("fwd_top_short_5d")
        s5s = (f"{s5*100:+.1f}%" if isinstance(s5, (int, float)) else "n/a")
        hit = r.get("direction_hit")
        hit_s = "HIT" if hit is True else ("MISS" if hit is False else "")
        lines.append(f"| {r['date']} | {r.get('action') or ''} | "
                      f"{r.get('conviction') or ''} | "
                      f"{r.get('top_buy') or ''} | {b5s} | "
                      f"{r.get('top_short') or ''} | {s5s} | "
                      f"{u5s} | {hit_s} |\n")
    lines.append("\n")
    return "".join(lines)


def _write_skipped_report(reason: str) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        f"# Layer 2 -- GPT-4o-mini brain backtest (SKIPPED)\n\n"
        f"_Run at {datetime.now().isoformat(timespec='seconds')}_\n\n"
        f"**Skipped:** {reason}\n\n"
        f"To enable this layer:\n\n"
        f"1. Create a fine-grained PAT at "
        f"https://github.com/settings/tokens with the `models:read` scope.\n"
        f"2. Set `GITHUB_TOKEN=<your token>` in your shell.\n"
        f"3. Re-run `python scripts/backtest_with_github_models.py`.\n",
        encoding="utf-8",
    )
    OUT_JSON.write_text(json.dumps({"skipped": reason}, indent=2),
                         encoding="utf-8")


# ---------------------------------------------------------------------------
# 7) Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-05-03",
                     help="Start date YYYY-MM-DD (default 2024-05-03).")
    ap.add_argument("--end",   default="2026-05-01",
                     help="End date YYYY-MM-DD (default 2026-05-01).")
    ap.add_argument("--weekday", type=int, default=4,
                     help="Weekday to sample (0=Mon..4=Fri, default 4=Fri).")
    ap.add_argument("--model", default="openai/gpt-4o-mini",
                     help="GitHub Models model name (default gpt-4o-mini).")
    ap.add_argument("--max-calls", type=int, default=140,
                     help="Hard cap on LLM calls (rate-limit safety, default 140).")
    ap.add_argument("--rate-delay", type=float, default=RATE_DELAY_S,
                     help=f"Seconds to sleep between calls "
                           f"(default {RATE_DELAY_S}).")
    ap.add_argument("--dry-run", action="store_true",
                     help="Print sampled dates and exit; no LLM calls.")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    dates = weekly_dates(start, end, weekday=args.weekday)
    print(f"Layer 2 backtest: {len(dates)} weekly dates "
          f"from {start} to {end} (weekday={args.weekday}).")

    if args.dry_run:
        for d in dates:
            print(f"  {d}")
        return 0

    client, err = _make_client()
    if client is None:
        print(f"[skipped] {err}")
        _write_skipped_report(err)
        return 0

    if len(dates) > args.max_calls:
        print(f"Capping to {args.max_calls} calls "
              f"(would have run {len(dates)}).")
        # Evenly spaced subset so we still get full window coverage.
        step = len(dates) / args.max_calls
        dates = [dates[int(i * step)] for i in range(args.max_calls)]

    records: list[dict] = []
    for i, d in enumerate(dates, 1):
        print(f"[{i}/{len(dates)}] {d} ...", end=" ", flush=True)
        try:
            r = run_one_date(client, args.model, d)
            records.append(r)
            if "error" in r:
                print(f"ERR ({r['error'][:80]})")
            elif "llm_error" in r:
                print(f"LLM_ERR ({r['llm_error'][:80]})")
            else:
                print(f"action={r.get('action')} "
                      f"top_buy={r.get('top_buy')}")
        except Exception as e:
            traceback.print_exc()
            records.append({"date": d.isoformat(),
                              "error": f"{type(e).__name__}: {e}"})

        # Persist after every call so a crash never loses progress.
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(records, indent=2, default=str),
                              encoding="utf-8")

        if i < len(dates) and args.rate_delay > 0:
            time.sleep(args.rate_delay)

    summary = _aggregate(records)
    md = _render(records, summary, args.model, start, end, args.weekday)
    OUT_MD.write_text(md, encoding="utf-8")

    print("\nSummary:")
    print(f"  Valid LLM decisions   : {summary['n_valid']}/"
          f"{summary['n_dates']}")
    if summary["direction_hit_rate_pct"] is not None:
        print(f"  Direction hit-rate    : "
              f"{summary['direction_hit_rate_pct']:.1f}%")
    if summary["mean_top_buy_fwd_5d_pct"] is not None:
        print(f"  Mean top_buy fwd 5d   : "
              f"{summary['mean_top_buy_fwd_5d_pct']:+.2f}%")
    if summary["mean_top_short_fwd_5d_pct"] is not None:
        print(f"  Mean top_short fwd 5d : "
              f"{summary['mean_top_short_fwd_5d_pct']:+.2f}%")
    print(f"\nReport : {OUT_MD}")
    print(f"JSON   : {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
