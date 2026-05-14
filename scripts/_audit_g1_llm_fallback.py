"""Gap-1 measurement: how often is the strategist actually the LLM
versus the deterministic fallback?

For each dated strategist file we have in git history, pull the
canonical commit for that date, parse the JSON, and record:
  - model field
  - fallback_used boolean
  - whether overlay log is populated

Reports the ratio across the full window and per-week. If
fallback_used is True >40% of the time, the "LLM" attribution in our
prior analysis is misleading: the system is mostly the rule engine
+ playbook overlays, with Claude's contribution being intermittent.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DATE_RE = re.compile(r"data/_strategist/(\d{4}-\d{2}-\d{2})\.json")


def _git_log_strategist() -> list[tuple[str, str, str]]:
    """Return list of (sha, date, path) for committed dated strategist files."""
    out = subprocess.check_output(
        ["git", "log", "--all", "--pretty=format:%H|%ad", "--date=short",
         "--name-only", "--", "data/_strategist/"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    entries: list[tuple[str, str, str]] = []
    current_sha = None
    current_date = None
    for line in out.splitlines():
        if "|" in line and len(line.split("|", 1)[0]) == 40:
            current_sha, current_date = line.split("|", 1)
            continue
        m = DATE_RE.search(line.strip())
        if m and current_sha and current_date:
            entries.append((current_sha, current_date, m.group(0)))
    return entries


def _git_show(sha: str, path: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{sha}:{path}"],
            cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def _classify(payload: dict) -> dict:
    model = payload.get("model") or ""
    fallback_used = bool(payload.get("fallback_used"))
    overlay = payload.get("playbook_overlay_log") or []
    has_overlay = bool(overlay)
    is_pure_llm = bool(model) and not fallback_used
    is_hybrid = bool(model) and fallback_used
    is_fallback_only = (model == "" or "fallback" in (model or "").lower()) \
                       and fallback_used
    return {
        "model": model,
        "fallback_used": fallback_used,
        "has_overlay": has_overlay,
        "n_overlays": len(overlay),
        "is_pure_llm": is_pure_llm,
        "is_hybrid": is_hybrid,
        "is_fallback_only": is_fallback_only,
    }


def main() -> int:
    entries = _git_log_strategist()
    print(f"Found {len(entries)} commits touching dated strategist files.")
    # Dedupe: pick the LATEST sha per date (most recent overwrite wins).
    by_date: dict[str, tuple[str, str]] = {}
    for sha, date, path in entries:
        # Only keep dated files (not _briefing/_per_stock)
        if not DATE_RE.match(path):
            continue
        # The first occurrence per date in the iteration order is the
        # newest because `git log` is chronological-desc.
        if date not in by_date:
            by_date[date] = (sha, path)
    print(f"Distinct dates with a dated strategist file: {len(by_date)}")

    records: list[dict] = []
    for date in sorted(by_date.keys()):
        sha, path = by_date[date]
        raw = _git_show(sha, path)
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rec = {"date": date, **_classify(payload)}
        records.append(rec)

    print()
    print(f"Parsed records: {len(records)}")
    print()
    print(f"{'date':<12} {'model':<35} {'fallback':<10} {'overlay':<8}")
    print("-" * 70)
    for r in records:
        print(f"{r['date']:<12} {r['model'][:33]:<35} "
              f"{str(r['fallback_used']):<10} {r['n_overlays']:<8}")

    n = len(records)
    if n == 0:
        print("\nNo records to summarize.")
        return 1

    n_pure_llm = sum(1 for r in records if r["is_pure_llm"])
    n_hybrid   = sum(1 for r in records if r["is_hybrid"])
    n_fallback = sum(1 for r in records if r["is_fallback_only"])
    n_overlay  = sum(1 for r in records if r["has_overlay"])
    n_fb_any   = sum(1 for r in records if r["fallback_used"])

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(f"Total dated strategist files: {n}")
    print(f"  Pure LLM (model set, fallback=False):     "
          f"{n_pure_llm:>3} ({n_pure_llm/n*100:.0f}%)")
    print(f"  Hybrid (model set, fallback=True):        "
          f"{n_hybrid:>3} ({n_hybrid/n*100:.0f}%)")
    print(f"  Fallback only (no LLM signal at all):     "
          f"{n_fallback:>3} ({n_fallback/n*100:.0f}%)")
    print(f"  Any fallback flag set:                    "
          f"{n_fb_any:>3} ({n_fb_any/n*100:.0f}%)")
    print(f"  Has playbook_overlay_log populated:       "
          f"{n_overlay:>3} ({n_overlay/n*100:.0f}%)")
    print()

    fallback_share = n_fb_any / n
    if fallback_share >= 0.40:
        print(f"[MEANINGFUL] Fallback fires on {fallback_share*100:.0f}% of "
              "days. The 'LLM strategist' framing is misleading: the system "
              "is primarily the rule engine + playbook overlays, with Claude "
              "contributing on a minority of days. ACTION: relabel UI, treat "
              "rule overlay as primary, treat LLM as enhancement.")
    elif fallback_share >= 0.20:
        print(f"[PARTIAL] Fallback fires on {fallback_share*100:.0f}% of "
              "days. Material but not dominant. ACTION: add a fallback "
              "frequency badge to the UI so users can see when Claude was "
              "skipped.")
    else:
        print(f"[NOT MEANINGFUL] Fallback fires only "
              f"{fallback_share*100:.0f}% of days. LLM is the primary "
              "decision source; my earlier concern was overstated.")
    print()
    model_counter = Counter(r["model"] for r in records)
    print("Model field histogram:")
    for m, c in model_counter.most_common():
        print(f"  {m!r:<50} {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
