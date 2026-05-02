"""Run the Master Strategist (top-layer Claude reasoning) and persist the
result to ``data/_strategist/<YYYY-MM-DD>.json`` + ``latest.json``.

Why this script exists
----------------------

``brain/master_strategist.py`` is callable from the Streamlit UI on
demand, but you usually want one fresh strategist call per day at
market open so the dashboard, the daily PDF brief, and any cron
consumer all see the same decision. This script is the cron entry
point.

Usage::

    # Default: Claude Sonnet 4.5 with 12k thinking budget
    python scripts/run_master_strategist.py

    # Heaviest reasoning (Claude Opus 4.5, 24k thinking budget)
    python scripts/run_master_strategist.py --deep

    # Custom thinking budget
    python scripts/run_master_strategist.py --thinking 16000

    # Don't write the cache (useful for one-off audits)
    python scripts/run_master_strategist.py --no-cache

Environment::

    ANTHROPIC_API_KEY    required for a real call; without it the
                         deterministic rule-based fallback runs.

Exit codes
----------

    0  success — decision written
    1  unrecoverable error (rare; the LLM-failed path falls back
       gracefully and still exits 0)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from brain import master_strategist as ms  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deep", action="store_true",
                    help="Use Claude Opus 4.5 + 24k thinking budget.")
    ap.add_argument("--thinking", type=int, default=None,
                    help="Override thinking budget (in tokens).")
    ap.add_argument("--max-tokens", type=int, default=6_000,
                    help="Upper bound on Claude's response per turn.")
    ap.add_argument("--no-cache", action="store_true",
                    help="Don't persist the decision to disk.")
    args = ap.parse_args()

    try:
        out = ms.decide_today(
            deep=args.deep,
            thinking_budget=args.thinking,
            max_tokens=args.max_tokens,
            write_cache=not args.no_cache,
        )
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"\nMaster Strategist  — {out.get('as_of')}")
    print(f"Model              : {out.get('model')}")
    print(f"Thinking budget    : {out.get('thinking_budget')}")
    print(f"Stance / conviction: {out.get('risk_stance')} / "
          f"{out.get('conviction')}")
    print(f"Headline           : {out.get('headline')}")
    print(f"Agrees w/ Phase-1  : {out.get('agrees_with_phase1')}")
    if not out.get("agrees_with_phase1", True):
        print(f"Override note      : {out.get('phase1_disagreement_note')}")

    actions = out.get("actions") or []
    if actions:
        print(f"\nActions ({len(actions)}):")
        for a in actions:
            sym = a.get("symbol") or "—"
            tw = a.get("target_weight_pct")
            tw_s = f"{tw:>5.1f}%" if isinstance(tw, (int, float)) else "  —  "
            print(f"  {a.get('bucket','HOLD'):>5}  "
                  f"{sym:<6}  {a.get('conviction','MEDIUM'):<6}  "
                  f"{tw_s}  {(a.get('reason') or '')[:80]}")

    if not args.no_cache:
        print(f"\nCached → {ms.cache_path()}")
        print(f"        {ms.CACHE_DIR / 'latest.json'}")

    if out.get("fallback_used"):
        print("\nNote: rule-based fallback ran (no ANTHROPIC_API_KEY or LLM "
              "call failed). Decision is mechanical only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
