"""Local equivalent of one .github/workflows/*.yml scheduled job.

Runs the same Python command(s) the GitHub Actions workflow runs, using this
machine's own .venv, then commits and pushes any resulting data changes with
the same "no changes -> skip, push -> retry with rebase" pattern the workflows
use. Meant to be invoked by Windows Task Scheduler (see register_tasks.ps1),
but safe to run by hand too:

    .venv\\Scripts\\python.exe scripts\\local_scheduler\\run_workflow.py --workflow eod

Why this exists: the GitHub Actions runners depend on secrets (ANTHROPIC_API_KEY
etc.) and a clean CI environment that have both broken independently of this
machine (see reports/master_strategist_2026-09-01.md for the incident). Running
the same scripts locally on a schedule means the data pipeline keeps moving even
when GitHub Actions itself is degraded, using whichever keys are actually valid
in the local .env.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "local_scheduler"
LOG_DIR.mkdir(parents=True, exist_ok=True)
PY = sys.executable  # the venv python currently running this script

# ---------------------------------------------------------------------------
# One entry per .github/workflows/*.yml. Mirrors the "run:" steps of the real
# workflow, minus "install deps" (the local .venv already has everything) and
# minus GitHub-only steps (workflow_dispatch inputs, gh workflow run dispatch
# — replaced below with a direct local call where it matters).
# ---------------------------------------------------------------------------
WORKFLOWS: dict[str, list[dict]] = {
    "macro_series": [
        {"cmd": [PY, "scripts/refresh_macro_series.py"]},
    ],
    "overnight": [
        {"cmd": [PY, "scripts/fetch_overnight_global.py"]},
    ],
    "predictions": [
        {"cmd": [PY, "scripts/generate_predictions.py"]},
        {"cmd": [PY, "scripts/_patch_pred_prices.py", "--latest"]},
        {"cmd": [PY, "scripts/_patch_pred_prices.py", "--check"], "allow_fail": True},
    ],
    "master_strategist": [
        {"cmd": [PY, "-m", "scripts.run_strategist_v2", "--llm"], "allow_fail": True},
        {"cmd": [PY, "scripts/run_master_strategist.py"], "only_if_prev_failed": True},
    ],
    "news_scoring": [
        {"cmd": [PY, "scripts/score_news_sentiment.py", "--per-feed", "8", "--batch", "8"]},
        {"cmd": [PY, "scripts/check_news_shocks.py"], "allow_fail": True, "shock_dispatch": True},
    ],
    "health_check": [
        {"cmd": [PY, "scripts/health_check.py"], "allow_fail": True, "commit": False},
    ],
    "macro_kpis": [
        {"cmd": [PY, "scripts/refresh_macro_kpis.py"]},
    ],
    "intraday_session": [
        {"cmd": [PY, "scripts/refresh_live_market.py"]},
        {"cmd": [PY, "scripts/score_news_sentiment.py", "--per-feed", "5", "--batch", "8", "--skip-health"]},
        {"cmd": [PY, "scripts/check_news_shocks.py"], "allow_fail": True, "shock_dispatch": True},
    ],
    "eod": [
        {"cmd": [PY, "scripts/backfill.py"]},
        {"cmd": [PY, "scripts/cache_fipi_daily.py"], "allow_fail": True},
        {"cmd": [PY, "scripts/check_predictions.py"]},
    ],
    "material_info": [
        {"cmd": [PY, "scripts/refresh_material_info.py"]},
    ],
    "dividend_calendar": [
        {"cmd": [PY, "scripts/refresh_dividend_calendar.py"], "allow_fail": True},
    ],
    "google_trends": [
        {"cmd": [PY, "scripts/refresh_google_trends.py"], "allow_fail": True},
    ],
    "fundamentals": [
        {"cmd": [PY, "scripts/refresh_fundamentals.py"]},
    ],
    "financial_results": [
        {"cmd": [PY, "-m", "scripts.extract_director_report"]},
    ],
    "ingest_mf_holdings": [
        {"cmd": [PY, "scripts/ingest_ahl_mf_holdings.py"]},
        {"cmd": [PY, "scripts/ingest_ahl_mf_holdings.py", "--validate"]},
    ],
    "ingest_amc_fmr": [
        {"cmd": [PY, "scripts/ingest_amc_fmr.py"]},
        {"cmd": [PY, "scripts/ingest_amc_fmr.py", "--validate"]},
    ],
    "validate_ranker": [
        {"cmd": [PY, "scripts/validate_ranker.py"], "allow_fail": True},
    ],
    "fundamentals_history": [
        {"cmd": [PY, "scripts/build_fundamentals_history.py"]},
    ],
}

# Monthly-cadence workflows are registered as DAILY Task Scheduler triggers
# (the module available on this machine has no native monthly trigger type)
# and gated here instead: the step list only actually runs on the right
# day-of-month (and month, for the quarterly one).
DAY_GATES: dict[str, dict] = {
    "ingest_mf_holdings": {"day_of_month": 5},
    "ingest_amc_fmr": {"day_of_month": 8},
    "validate_ranker": {"day_of_month": 1, "months": {1, 4, 7, 10}},
    "fundamentals_history": {"day_of_month": 1},  # monthly is cheap+idempotent; catches new filings promptly
}


def _gate_passes(name: str) -> bool:
    gate = DAY_GATES.get(name)
    if gate is None:
        return True
    today = datetime.now().astimezone()
    if today.day != gate["day_of_month"]:
        return False
    months = gate.get("months")
    if months is not None and today.month not in months:
        return False
    return True


def _load_dotenv() -> None:
    """Populate os.environ from .env (python-dotenv isn't installed locally;
    GitHub Actions injects these via workflow `env:` blocks instead, so a
    local run needs the same values loaded manually)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val


def _log(fh, msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    fh.write(line + "\n")
    fh.flush()


def _run_step(fh, step: dict, prev_failed: bool) -> tuple[bool, int]:
    if step.get("only_if_prev_failed") and not prev_failed:
        _log(fh, f"SKIP (previous step succeeded): {' '.join(step['cmd'])}")
        return True, 0
    cmd = step["cmd"]
    _log(fh, f"RUN: {' '.join(cmd)}")
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        cmd, cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    fh.write(proc.stdout or "")
    fh.flush()
    print(proc.stdout or "", end="")
    ok = proc.returncode == 0
    if not ok:
        _log(fh, f"EXIT CODE {proc.returncode} for: {' '.join(cmd)}")
    if step.get("shock_dispatch") and proc.returncode == 7:
        _log(fh, "NEWS SHOCK DETECTED (exit 7) -- running predictions.py immediately")
        run_workflow_steps(fh, "predictions", WORKFLOWS["predictions"])
        ok = True  # exit 7 is a signal, not a failure
    allow_fail = step.get("allow_fail", False)
    return (ok or allow_fail), proc.returncode


def run_workflow_steps(fh, name: str, steps: list[dict]) -> bool:
    prev_failed = False
    all_ok = True
    for step in steps:
        ok, _rc = _run_step(fh, step, prev_failed)
        prev_failed = not ok
        all_ok = all_ok and ok
    return all_ok


def _git(fh, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    fh.write(proc.stdout or "")
    return proc


def _commit_and_push(fh, workflow_name: str) -> None:
    _git(fh, "add", "-A", "--", "data/", "models/")
    diff = _git(fh, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        _log(fh, "No data changes -- skipping commit.")
        return
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    msg = f"data: {workflow_name} (local scheduler) {date_str}"
    commit = _git(fh, "commit", "-m", msg)
    _log(fh, commit.stdout.strip())
    for attempt in range(1, 6):
        push = _git(fh, "push")
        if push.returncode == 0:
            _log(fh, "Push OK.")
            return
        _log(fh, f"Push attempt {attempt} failed; rebasing on origin/main...")
        _git(fh, "pull", "--rebase", "--autostash", "origin", "main")
        time.sleep(attempt * 2)
    _log(fh, "Push failed after 5 attempts -- leaving commit local for manual resolution.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True, choices=sorted(WORKFLOWS.keys()))
    args = parser.parse_args()

    _load_dotenv()

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{args.workflow}_{date_str}.log"
    with open(log_path, "w", encoding="utf-8") as fh:
        _log(fh, f"=== {args.workflow} (local scheduler run) ===")
        if not _gate_passes(args.workflow):
            _log(fh, f"Not the scheduled day for {args.workflow} -- skipping (monthly/quarterly gate).")
            return 0
        steps = WORKFLOWS[args.workflow]
        ok = run_workflow_steps(fh, args.workflow, steps)
        should_commit = not any(s.get("commit") is False for s in steps)
        if should_commit:
            _commit_and_push(fh, args.workflow)
        _log(fh, f"=== {args.workflow} finished: {'OK' if ok else 'HAD FAILURES (see log)'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
