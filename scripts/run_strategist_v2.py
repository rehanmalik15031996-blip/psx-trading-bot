"""CLI entry point for the v2 multi-agent strategist pipeline.

Usage:
    python -m scripts.run_strategist_v2 [--llm] [--account 1000000]

Writes:
    data/_strategist/YYYY-MM-DD.json         (v1 schema, UI back-compat)
    data/_strategist/YYYY-MM-DD_v2.json      (v2 with per-tab guidance)
    data/_strategist/latest.json             (always-latest, v1)
    data/_strategist/latest_v2.json          (always-latest, v2)
    data/_health/strategist_v2.json          (health badge)

Designed to be called from the daily GitHub Action workflow after the
predictions, macro_impact, and verdict_universe jobs have completed.
"""
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true",
                        help="Allow optional Claude refinement of narratives. "
                             "Requires ANTHROPIC_API_KEY. Falls back to "
                             "rule-based output if the API call fails.")
    parser.add_argument("--account", type=float, default=None,
                        help="Account NAV in PKR; used by the position-plan "
                             "calculator to compute absolute size in PKR.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Do not persist the decision (testing only).")
    args = parser.parse_args()

    print("Master Strategist v2 — multi-agent pipeline")
    print(f"  use_llm:       {args.llm}")
    print(f"  account_pkr:   {args.account}")
    print(f"  write_cache:   {not args.no_cache}")
    print()

    t0 = time.time()

    from brain.agents.pipeline import run_pipeline
    out = run_pipeline(
        use_llm=args.llm,
        account_size_pkr=args.account,
        write_cache=not args.no_cache,
    )

    elapsed = time.time() - t0
    print(f"\n[{elapsed:.1f}s] Pipeline complete.")
    print(f"  headline:      {out.get('headline')}")
    print(f"  regime:        {out.get('regime')} "
          f"({out.get('regime_confidence')})")
    print(f"  model:         {out.get('model')}")
    print(f"  fallback:      {out.get('fallback_used')}")
    print(f"  long ideas:    {out['long_ideas']['count']}")
    print(f"  short ideas:   {out['short_ideas']['count']}")
    print(f"  sectors:       {len(out['sector_view']['sectors'])}")
    print(f"  watchlist:     {out['watchlist']['count']}")
    print(f"  risks:         {out['risks_today']['count']}")
    print(f"  events:        {out['events_intelligence']['count']}")
    if out.get("pipeline_errors"):
        print(f"  errors:        {out['pipeline_errors']}")
        return 1

    # Print headline ideas
    print()
    print("  Top 3 long ideas:")
    for idea in (out["long_ideas"]["ideas"] or [])[:3]:
        pp = idea.get("position_plan") or {}
        print(f"    {idea['symbol']:<7} "
              f"[{idea['action']:<4} {idea['conviction']:<7}] "
              f"score {idea['score']:+.2f}  "
              f"stop {pp.get('stop_loss_pct')}%  "
              f"target {pp.get('target_pct')}%  "
              f"size {pp.get('position_size_pct')}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
