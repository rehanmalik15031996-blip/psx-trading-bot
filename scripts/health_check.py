"""Daily data-freshness gate.

Reads ``data/_health/<workflow>.json`` for every workflow in
``config.data_slas.SLAS`` and, for each, computes the age of the
last successful run. If any source breaches its red SLA, the script
exits non-zero — failing the wrapping workflow, which automatically
triggers GitHub's built-in "workflow failure" email to the repo
owner.

The same logic powers the green / amber / red badges in the
Streamlit System Health tab, so a single SLA table drives both
alerting and visualisation.

Run modes
---------
``python scripts/health_check.py``
    Default. Prints a per-workflow table; exits 1 if any RED breach.

``python scripts/health_check.py --strict``
    Also fails on AMBER breaches. Used for paranoid pre-deploy
    runs; the scheduled health-check workflow stays in default mode
    so a single amber feed does not page you at 04:40 UTC.

``python scripts/health_check.py --json``
    Print the report as a single JSON payload (UI / scripting use).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HEALTH_DIR = ROOT / "data" / "_health"


def _load_status(workflow: str) -> dict | None:
    p = HEALTH_DIR / f"{workflow}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _age_seconds(as_of_iso: str | None) -> float | None:
    if not as_of_iso:
        return None
    try:
        ts = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


def _is_weekday() -> bool:
    return datetime.now(timezone.utc).weekday() < 5


def _is_intraday_window() -> bool:
    """Return True if PSX is currently in a trading session (09:30-15:30 PKT)."""
    pkt_now = datetime.now(timezone.utc) + timedelta(hours=5)
    if pkt_now.weekday() >= 5:
        return False
    open_t = pkt_now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = pkt_now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= pkt_now <= close_t


def _humanize(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.0f}m"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}d"


def evaluate() -> list[dict]:
    """Return one row per SLA describing its current status."""
    from config.data_slas import SLAS

    rows: list[dict] = []
    for sla in SLAS:
        body = _load_status(sla.workflow)
        age = _age_seconds(body.get("as_of") if body else None)
        last_ok = bool(body.get("ok")) if body else False

        # Decide if this SLA is enforced right now.
        if sla.weekday_only and not _is_weekday():
            badge = "GREEN"  # weekend, PSX closed
            reason = "weekend (PSX closed) — SLA not enforced"
        elif sla.intraday_only and not _is_intraday_window():
            badge = "GREEN"
            reason = "outside trading session — SLA not enforced"
        elif body is None:
            # Treat 'never observed' as AMBER not RED so the very
            # first scheduled run after this iteration deploys does
            # NOT immediately email the repo owner. Once each
            # workflow has fired once and written its status file,
            # the normal age-based logic takes over.
            badge = "AMBER"
            reason = ("no health file yet — workflow has not "
                      "written status since this iteration deployed")
        elif not last_ok:
            badge = "RED"
            reason = (f"last run reported failure: "
                      f"{body.get('note', '(no note)')}")
        elif age is None:
            badge = "AMBER"
            reason = "could not parse last-run timestamp"
        elif age >= sla.red_seconds:
            badge = "RED"
            reason = (f"last success was {_humanize(age)} ago "
                      f"(red threshold {_humanize(sla.red_seconds)})")
        elif age >= sla.amber_seconds:
            badge = "AMBER"
            reason = (f"last success was {_humanize(age)} ago "
                      f"(amber threshold {_humanize(sla.amber_seconds)})")
        else:
            badge = "GREEN"
            reason = (f"last success {_humanize(age)} ago "
                      f"({body.get('note', '')})")

        rows.append({
            "workflow":     sla.workflow,
            "badge":        badge,
            "reason":       reason,
            "age_seconds":  age,
            "last_ok":      last_ok,
            "note":         (body or {}).get("note", ""),
            "as_of":        (body or {}).get("as_of", ""),
            "amber_seconds": sla.amber_seconds,
            "red_seconds":   sla.red_seconds,
            "schedule_note": sla.note,
        })
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strict", action="store_true",
                    help="Treat AMBER as failure too.")
    p.add_argument("--json", action="store_true",
                    help="Emit a single JSON document instead of a table.")
    args = p.parse_args()

    rows = evaluate()
    if args.json:
        print(json.dumps({"as_of": datetime.now(timezone.utc).isoformat(
                              timespec="seconds"),
                          "rows":  rows}, indent=2, default=str))
    else:
        print(f"{'WORKFLOW':<20} {'BADGE':<6} {'AGE':<10} REASON")
        print("-" * 90)
        for r in rows:
            age = _humanize(r["age_seconds"])
            print(f"{r['workflow']:<20} {r['badge']:<6} {age:<10} "
                  f"{r['reason'][:70]}")

    bad_levels = ("RED",)
    if args.strict:
        bad_levels = ("RED", "AMBER")
    bad = [r for r in rows if r["badge"] in bad_levels]
    if bad:
        print()
        print(f"FAIL: {len(bad)} workflow(s) breaching SLA — "
              f"{[b['workflow'] for b in bad]}")
        return 1

    print()
    print(f"OK: all {len(rows)} workflows within SLA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
