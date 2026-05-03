"""Layer 2 of the 24-month free backtest -- Google Gemini brain (free tier).

Mirrors `scripts/backtest_with_github_models.py` but uses Google Gemini
instead of GitHub Models. Gemini's free tier is generous:

* gemini-2.5-flash : 10 RPM / 250k TPM / 250 RPD  (default -- best balance)
* gemini-2.5-pro   : 5 RPM / 250k TPM / 100 RPD   (slower but stronger)
* gemini-2.0-flash : 15 RPM / 1M TPM / 1500 RPD   (fastest, more dates/day)

Walks every Friday from --start to --end (~100 weekly samples by default),
replays the full Master Strategist briefing, runs the production
briefing-compression pass, then asks Gemini to produce a strategist-format
JSON decision. Each decision is scored against realised forward 5d / 21d
returns.

Outputs:

* ``data/_health/backtest_gemini.json`` -- per-date raw records.
* ``data/_health/backtest_gemini.md``   -- summary + per-date table.

Cost: $0 (Gemini free tier).

Reads the API key from (in priority order):

  1. ``GEMINI_API_KEY`` env var
  2. ``GOOGLE_API_KEY`` env var
  3. ``--env-file`` argument (defaults to ``env (1)`` in the project root)

Usage::

    python scripts/backtest_with_gemini.py
    python scripts/backtest_with_gemini.py --model gemini-2.5-pro --max-calls 50
    python scripts/backtest_with_gemini.py --weekday 4 --max-calls 100
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
from scripts.historical_test_playbook import _replay_active_events
from scripts.replay_briefing import forward_universe_return, replay_briefing

OUT_JSON = ROOT / "data" / "_health" / "backtest_gemini.json"
OUT_MD   = ROOT / "data" / "_health" / "backtest_gemini.md"

DEFAULT_ENV_FILE = ROOT / "env (1)"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_BRIEFING_CHARS = 200_000


# ---------------------------------------------------------------------------
# 1) Env-file parsing -- read GEMINI_API_KEY from the user's existing file
# ---------------------------------------------------------------------------
def _load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a .env-style file and return key/value pairs."""
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and not k.startswith("#"):
            out[k] = v
    return out


def _resolve_api_key(env_file: Path | None) -> tuple[str | None, str]:
    """Return (api_key, source-description) using env vars first, then file."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.environ.get(var)
        if v:
            return v, f"env var {var}"
    if env_file is not None:
        env = _load_env_file(env_file)
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            v = env.get(var)
            if v:
                return v, f"{env_file.name}:{var}"
    return None, "no key found"


# ---------------------------------------------------------------------------
# 2) Date sample
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
# 3) Per-stock forward return (for top_buy / top_short scoring)
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
# 4) Gemini call
# ---------------------------------------------------------------------------
def _make_client(api_key: str):
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            f"google-genai package not installed: {e}. "
            f"Install with: pip install google-genai")
    return genai.Client(api_key=api_key)


def _call_strategist(client, briefing: dict, model: str) -> tuple[dict, dict]:
    """Send briefing to Gemini and parse JSON decision.

    Returns (decision_dict, raw_meta) where raw_meta has token-usage info.
    """
    from google.genai import types as gtypes

    user_msg = (
        "Today's briefing JSON follows. Output ONLY a single JSON object "
        "with the exact schema specified in your system prompt. No prose, "
        "no markdown fences, just JSON.\n\n"
        f"```json\n{json.dumps(briefing, default=str)[:MAX_BRIEFING_CHARS]}\n```"
    )

    # Build config. For Gemini 2.5 models, thinking tokens count toward the
    # output budget, so we either disable thinking (preferred for backtest
    # speed) or bump max_output_tokens far above what we'd otherwise need.
    cfg_kwargs = dict(
        system_instruction=ms.STRATEGIST_SYSTEM,
        temperature=0.2,
        max_output_tokens=8000,
        response_mime_type="application/json",
    )
    if "2.5" in model:
        try:
            cfg_kwargs["thinking_config"] = gtypes.ThinkingConfig(
                thinking_budget=0)
        except Exception:
            pass
    config = gtypes.GenerateContentConfig(**cfg_kwargs)
    resp = client.models.generate_content(
        model=model, contents=user_msg, config=config,
    )
    text = (resp.text or "").strip() if hasattr(resp, "text") else ""
    if not text:
        # Fallback: dig into candidates
        try:
            cands = getattr(resp, "candidates", []) or []
            if cands:
                parts = getattr(cands[0].content, "parts", []) or []
                text = "\n".join(getattr(p, "text", "") or "" for p in parts).strip()
        except Exception:
            pass

    meta = {"text_len": len(text)}
    try:
        usage = getattr(resp, "usage_metadata", None)
        if usage:
            meta["prompt_tokens"]    = getattr(usage, "prompt_token_count", None)
            meta["candidate_tokens"] = getattr(usage, "candidates_token_count", None)
            meta["total_tokens"]     = getattr(usage, "total_token_count", None)
    except Exception:
        pass

    return _parse_decision(text), meta


def _parse_decision(text: str) -> dict:
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
# 5) Decision-shape extractors (tolerant of variations)
# ---------------------------------------------------------------------------
def _extract_action(decision: dict) -> str | None:
    """Extract the top-level stance. Strategist schema uses `risk_stance`."""
    if not isinstance(decision, dict):
        return None
    for key in ("risk_stance", "action", "decision", "stance", "call"):
        v = decision.get(key)
        if isinstance(v, str) and v:
            return v.upper()
    return None


def _extract_top_buy(decision: dict) -> str | None:
    """Strategist schema: actions[].bucket in (BUY, ADD, HOLD, TRIM, AVOID, SHORT, WATCH)."""
    if not isinstance(decision, dict):
        return None
    actions = decision.get("actions")
    if isinstance(actions, list):
        for a in actions:
            if not isinstance(a, dict):
                continue
            bucket = (a.get("bucket") or a.get("action") or "").upper()
            if bucket in ("BUY", "ADD", "OVERWEIGHT", "LONG") and a.get("symbol"):
                return str(a["symbol"]).upper()
    for key in ("top_buy", "top_pick", "primary_buy", "buy", "long_pick"):
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
            bucket = (a.get("bucket") or a.get("action") or "").upper()
            if bucket in ("SELL", "TRIM", "AVOID", "UNDERWEIGHT", "SHORT") \
                    and a.get("symbol"):
                return str(a["symbol"]).upper()
    for key in ("top_short", "top_sell", "primary_short", "short", "short_pick"):
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
# 6) Per-date pipeline
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
        decision, meta = _call_strategist(client, compressed, model=model)
    except Exception as e:
        record["llm_error"] = f"{type(e).__name__}: {e}"
        return record

    record["decision_raw"] = decision
    record["llm_meta"] = meta
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

    fwd5 = record["fwd_universe_5d"]
    if isinstance(fwd5, (int, float)) and record["action"]:
        a = record["action"]
        if a in ("BUY", "LONG", "BULLISH", "ACCUMULATE", "ADD",
                   "AGGRESSIVE"):
            record["direction_hit"] = (fwd5 > 0)
        elif a in ("SELL", "SHORT", "BEARISH", "DISTRIBUTE", "REDUCE",
                     "CASH", "TRIM", "DEFENSIVE"):
            record["direction_hit"] = (fwd5 < 0)
        elif a in ("HOLD", "NEUTRAL", "WAIT", "NORMAL", "CAUTIOUS"):
            record["direction_hit"] = (abs(fwd5) < 0.02)
        else:
            record["direction_hit"] = (abs(fwd5) < 0.02)
    return record


# ---------------------------------------------------------------------------
# 7) Aggregation + render
# ---------------------------------------------------------------------------
def _aggregate(records: list[dict]) -> dict:
    n = len(records)
    valid = [r for r in records if "decision_raw" in r and "error" not in r
                and "llm_error" not in r]
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

    pick_alpha_5d = []
    for r in valid:
        b5 = r.get("fwd_top_buy_5d")
        u5 = r.get("fwd_universe_5d")
        if isinstance(b5, (int, float)) and isinstance(u5, (int, float)):
            pick_alpha_5d.append((b5 - u5) * 100.0)

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
        "mean_pick_alpha_5d_pct": (sum(pick_alpha_5d) / len(pick_alpha_5d)
                                    if pick_alpha_5d else None),
        "n_top_buy_scored":   len(buy_fwd5),
        "n_top_short_scored": len(short_fwd5),
        "n_pick_alpha_scored": len(pick_alpha_5d),
    }


def _render(records: list[dict], summary: dict, model: str,
             start: date, end: date, weekday: int,
             key_source: str) -> str:
    lines: list[str] = []
    lines.append(f"# Layer 2 -- Gemini brain backtest\n\n")
    lines.append(f"_Run at {datetime.now().isoformat(timespec='seconds')}_\n\n")
    lines.append(f"Model: `{model}` via Google Gemini Free Tier "
                  f"(API key from {key_source}).\n")
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
    if summary.get("mean_pick_alpha_5d_pct") is not None:
        lines.append(f"| **Mean top-pick alpha vs universe (5d)** | "
                      f"{summary['mean_pick_alpha_5d_pct']:+.2f}pp "
                      f"({summary['n_pick_alpha_scored']} scored) |\n")
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
        if "llm_error" in r:
            lines.append(f"| {r['date']} | LLM_ERR | | | | | | | "
                          f"`{r['llm_error'][:60]}` |\n")
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


# ---------------------------------------------------------------------------
# 8) Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-05-03",
                     help="Start date YYYY-MM-DD (default 2024-05-03).")
    ap.add_argument("--end",   default="2026-05-01",
                     help="End date YYYY-MM-DD (default 2026-05-01).")
    ap.add_argument("--weekday", type=int, default=4,
                     help="Weekday to sample (0=Mon..4=Fri, default 4=Fri).")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                     help=f"Gemini model (default {DEFAULT_MODEL}). "
                          f"Try gemini-2.5-pro for best quality (slower).")
    ap.add_argument("--max-calls", type=int, default=104,
                     help="Hard cap on LLM calls (default 104 = ~2 years of "
                          "weekly Fridays). Daily quota is 250 for "
                          "gemini-2.5-flash.")
    ap.add_argument("--rate-delay", type=float, default=6.5,
                     help="Seconds to sleep between calls (default 6.5 -- "
                          "stays under 10 RPM for gemini-2.5-flash).")
    ap.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                     help=f"Path to .env file with GEMINI_API_KEY "
                          f"(default '{DEFAULT_ENV_FILE.name}' in repo root).")
    ap.add_argument("--dry-run", action="store_true",
                     help="Print sampled dates and exit; no LLM calls.")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    dates = weekly_dates(start, end, weekday=args.weekday)
    print(f"Layer 2 (Gemini): {len(dates)} weekly dates "
          f"from {start} to {end} (weekday={args.weekday}).")

    if args.dry_run:
        for d in dates:
            print(f"  {d}")
        return 0

    env_file = Path(args.env_file) if args.env_file else None
    api_key, key_source = _resolve_api_key(env_file)
    if not api_key:
        print(f"[error] No Gemini API key found. Tried env vars "
              f"GEMINI_API_KEY / GOOGLE_API_KEY and file {env_file}.")
        return 1
    print(f"[auth] Using key from {key_source} "
          f"(prefix: {api_key[:7]}..., len={len(api_key)})")

    try:
        client = _make_client(api_key)
    except Exception as e:
        print(f"[error] {e}")
        return 1

    if len(dates) > args.max_calls:
        step = len(dates) / args.max_calls
        dates = [dates[int(i * step)] for i in range(args.max_calls)]
        print(f"Capped to {args.max_calls} calls (evenly spaced).")

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
                tb = r.get("top_buy") or "_"
                act = r.get("action") or "?"
                tt = (r.get("llm_meta") or {}).get("total_tokens", "?")
                print(f"action={act} top_buy={tb} tokens={tt}")
        except Exception as e:
            traceback.print_exc()
            records.append({"date": d.isoformat(),
                              "error": f"{type(e).__name__}: {e}"})

        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(records, indent=2, default=str),
                              encoding="utf-8")

        if i < len(dates) and args.rate_delay > 0:
            time.sleep(args.rate_delay)

    summary = _aggregate(records)
    md = _render(records, summary, args.model, start, end, args.weekday,
                  key_source)
    OUT_MD.write_text(md, encoding="utf-8")

    print("\nSummary:")
    print(f"  Valid LLM decisions   : {summary['n_valid']}/"
          f"{summary['n_dates']}")
    if summary["direction_hit_rate_pct"] is not None:
        print(f"  Direction hit-rate    : "
              f"{summary['direction_hit_rate_pct']:.1f}%")
    if summary["mean_pick_alpha_5d_pct"] is not None:
        print(f"  Mean top-pick alpha   : "
              f"{summary['mean_pick_alpha_5d_pct']:+.2f}pp")
    if summary["mean_top_buy_fwd_5d_pct"] is not None:
        print(f"  Mean top_buy fwd 5d   : "
              f"{summary['mean_top_buy_fwd_5d_pct']:+.2f}%")
    print(f"\nReport : {OUT_MD}")
    print(f"JSON   : {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
