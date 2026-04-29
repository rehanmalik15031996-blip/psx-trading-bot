"""Per-workflow health-status helper.

Single function ``write_status`` invoked at the end of every refresh
script. Writes two artifacts:

  - ``data/_health/<workflow>.json`` — the latest run summary,
    overwritten each run. Read by ``scripts/health_check.py`` and by
    ``ui/system_health.py``.
  - ``data/_health/_history.parquet`` — append-only audit log used
    for the 30-day sparkline on the System Health tab.

The helper is intentionally tiny and dependency-light. It must NEVER
raise into the calling refresh script — the worst case is "we did
not record health for this run" not "the refresh job failed because
the health helper crashed".
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HEALTH_DIR = ROOT / "data" / "_health"


def _history_path(workflow: str) -> Path:
    """Per-workflow history file.

    A single shared ``_history.parquet`` sounds simpler but causes
    rebase conflicts whenever two workflows run concurrently
    (which happens often — news_scoring + intraday_session, or any
    EOD chain). One file per workflow eliminates the race.
    """
    return HEALTH_DIR / f"_history_{workflow}.parquet"

VALID_WORKFLOWS: set[str] = {
    "macro_series", "macro_kpis", "overnight", "news_scoring",
    "predictions", "eod", "intraday_session", "material_info",
    "fundamentals", "financial_results",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_status(
    workflow: str,
    ok: bool,
    note: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Record the outcome of a workflow run.

    Parameters
    ----------
    workflow : str
        One of :data:`VALID_WORKFLOWS`. Mismatched names still write,
        but emit a warning so a typo gets caught early.
    ok : bool
        Whether the run succeeded. Used by ``health_check.py`` to
        decide whether to count this as a green / red badge.
    note : str
        One-line human description (e.g. ``"7 series, latest 2026-04-30"``).
    payload : dict, optional
        Free-form metadata. Common fields: ``rows``, ``last_date``,
        ``per_source``, ``errors``. The file size is capped at 16 KB
        so a runaway payload cannot bloat the repo.
    """
    try:
        if workflow not in VALID_WORKFLOWS:
            print(f"  WARN: _health.write_status: unknown workflow "
                  f"key {workflow!r}; expected one of "
                  f"{sorted(VALID_WORKFLOWS)}",
                  file=sys.stderr)

        HEALTH_DIR.mkdir(parents=True, exist_ok=True)

        body: dict[str, Any] = {
            "workflow": workflow,
            "ok":       bool(ok),
            "note":     str(note)[:300],
            "as_of":    _now_iso(),
            "github": {
                "run_id":     os.environ.get("GITHUB_RUN_ID", ""),
                "run_number": os.environ.get("GITHUB_RUN_NUMBER", ""),
                "ref":        os.environ.get("GITHUB_REF", ""),
                "sha":        os.environ.get("GITHUB_SHA", ""),
            },
            "payload": payload or {},
        }
        # Cap the payload — never let a runaway log bloat the repo.
        s = json.dumps(body, indent=2, default=str)
        if len(s) > 16_000:
            body["payload"] = {"truncated": True,
                                 "original_size": len(s)}
            s = json.dumps(body, indent=2, default=str)
        (HEALTH_DIR / f"{workflow}.json").write_text(s, encoding="utf-8")

        _append_history(body)
    except Exception as e:
        # NEVER let the health helper fail the refresh script.
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)


def _append_history(body: dict[str, Any]) -> None:
    """Append a row to the rolling per-workflow history parquet."""
    try:
        import pandas as pd
    except Exception:
        # If pandas is missing (it shouldn't be in CI) skip the
        # history step. The single-row JSON is still authoritative.
        return

    history_path = _history_path(body["workflow"])
    row = {
        "as_of":    body["as_of"],
        "workflow": body["workflow"],
        "ok":       body["ok"],
        "note":     body["note"],
        "run_id":   body.get("github", {}).get("run_id", ""),
    }
    df_new = pd.DataFrame([row])
    if history_path.exists():
        try:
            df_old = pd.read_parquet(history_path)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()
    df_all = pd.concat([df_old, df_new], ignore_index=True)
    # Trim to last 90 calendar days to keep the file small.
    try:
        df_all["_ts"] = pd.to_datetime(df_all["as_of"], utc=True,
                                          errors="coerce")
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        df_all = df_all[df_all["_ts"] >= cutoff].drop(columns=["_ts"])
    except Exception:
        df_all = df_all.tail(2000)
    df_all.to_parquet(history_path, index=False)
