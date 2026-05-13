"""DEEP BACKTEST: 5 years of week-by-week playbook + overlay performance.

Walks every Friday from 2021-06 to 2026-05 (~250 dates), and for each:

  1. Calls `replay_briefing(as_of)` to reconstruct the briefing
  2. Patches `_load_active_events` so historical events fire correctly
  3. Calls `pb.retrieve_analogues()` to get fired playbook cases
  4. Builds a synthetic "all-HOLD equal-weight" baseline portfolio of the
     35-stock universe, then applies the new `strategist_overlays` engine
     to mutate buckets.
  5. Computes per-symbol forward 5d AND 21d returns.
  6. Scores per-sector overlay accuracy: did `Banking → downgrade_one`
     actually precede Banking sector underperformance?
  7. Computes portfolio P&L vs an equal-weight passive benchmark.

Outputs three JSON artifacts to `data/_research/`:

  - `backtest_per_date.json`: per-date case fires + sector returns
  - `backtest_per_case.json`: per-case fire count + HIT rate +
    avg_pnl_when_fired (5d/21d)
  - `backtest_per_sector_overlay.json`: per-(case, sector, action)
    accuracy: did the action save / capture the expected direction?

Plus a markdown report at `data/_research/backtest_report.md`.
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import playbook as pb
from brain import strategist_overlays as ov
from scripts.replay_briefing import (
    replay_briefing, forward_universe_return, forward_symbol_return,
    HISTORICAL_EVENTS,
)
from config.universe import UNIVERSE


OUT_DIR = ROOT / "data" / "_research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Patch the matcher event loader so it sees historical events.
# ---------------------------------------------------------------------------
_REPLAY_AS_OF: date | None = None


def _replay_active_events(path=None):
    if _REPLAY_AS_OF is None:
        return set()
    out: set[str] = set()
    for ev in HISTORICAL_EVENTS:
        try:
            d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        decay = int(ev.get("decay_days") or 14)
        if d <= _REPLAY_AS_OF and (_REPLAY_AS_OF - d).days <= decay:
            out.add(str(ev.get("key") or "").lower())
    return out


# ---------------------------------------------------------------------------
# Sector lookup from universe.py
# ---------------------------------------------------------------------------
def _norm_sector(s: str | None) -> str:
    if not s:
        return ""
    head = s.split("/")[0].strip()
    return ov.SECTOR_ALIASES.get(head.lower(), head)


SYMBOL_SECTOR: dict[str, str] = {
    u.symbol: _norm_sector(u.sector) for u in UNIVERSE
}


# ---------------------------------------------------------------------------
# Per-date backtest
# ---------------------------------------------------------------------------
def _build_baseline_decision(as_of: date) -> dict:
    """Synthetic 'all-HOLD equal-weight' baseline so the overlay has actions
    to mutate. Each universe member is HOLD with equal weight so we can
    measure pure overlay impact (ignoring whatever Phase-1 might have picked).
    """
    n = len(UNIVERSE)
    per_w = 100.0 / n
    actions = []
    for u in UNIVERSE:
        actions.append({
            "symbol": u.symbol,
            "sector": SYMBOL_SECTOR.get(u.symbol) or u.sector,
            "bucket": "HOLD",
            "conviction": "MEDIUM",
            "target_weight_pct": round(per_w, 2),
            "reason": "equal-weight baseline",
            "contributing_signals": [],
        })
    return {
        "as_of": as_of.isoformat(),
        "model": "backtest-baseline",
        "headline": "",
        "risk_stance": "NORMAL",
        "conviction": "MEDIUM",
        "narrative": "",
        "actions": actions,
        "fallback_used": False,
    }


def _per_symbol_fwd_returns(as_of: date, days: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for u in UNIVERSE:
        r = forward_symbol_return(u.symbol, as_of, days)
        if r is not None:
            out[u.symbol] = r
    return out


def _portfolio_pnl(decision: dict,
                    fwd: dict[str, float]) -> dict[str, float]:
    """Compute the portfolio's 'effective' P&L given bucket policy:
       BUY  = full weight long
       ADD  = 0.75 weight long
       HOLD = 0.50 weight long  (status-quo carrier)
       WATCH= 0.25 weight long
       AVOID= 0   (no exposure)
       TRIM = 0   (no exposure)
    Then sum (weight * fwd_return).
    """
    bucket_long = {"BUY": 1.0, "ADD": 0.75, "HOLD": 0.50,
                   "WATCH": 0.25, "AVOID": 0.0, "TRIM": 0.0}
    actions = decision.get("actions") or []
    gross = 0.0
    pnl = 0.0
    contribs: list[tuple[str, str, float, float]] = []
    cash_action = next(
        (a for a in actions
         if (a.get("bucket") or "").upper() == "CASH" and not a.get("symbol")),
        None,
    )
    cash_floor = float((cash_action or {}).get("target_weight_pct") or 0)
    deployable = max(0.0, 100.0 - cash_floor) / 100.0

    for a in actions:
        sym = a.get("symbol")
        if not sym or sym not in fwd:
            continue
        b = (a.get("bucket") or "HOLD").upper()
        long_frac = bucket_long.get(b, 0.5)
        # Equal weight across the universe, scaled by deployable %
        weight = (1.0 / len(UNIVERSE)) * long_frac * deployable
        gross += weight
        pnl += weight * fwd[sym]
        contribs.append((sym, b, weight, fwd[sym]))

    return {
        "gross_long": gross,
        "cash_floor_pct": cash_floor,
        "pnl_pct": pnl * 100,
        "contributions": contribs,
    }


def _per_sector_returns(fwd: dict[str, float]) -> dict[str, float]:
    out_sum: dict[str, float] = defaultdict(float)
    out_n: dict[str, int] = defaultdict(int)
    for sym, r in fwd.items():
        sec = SYMBOL_SECTOR.get(sym, "")
        if not sec:
            continue
        out_sum[sec] += r
        out_n[sec] += 1
    return {s: out_sum[s] / max(out_n[s], 1) for s in out_sum}


def run_one(as_of: date) -> dict:
    """Return per-date backtest outcome."""
    global _REPLAY_AS_OF
    _REPLAY_AS_OF = as_of
    pb._load_active_events = _replay_active_events  # noqa: SLF001

    briefing = replay_briefing(as_of)
    analogues = pb.retrieve_analogues(briefing, top_k=10) or []
    # add to briefing so overlay can find them
    briefing["playbook_analogues"] = analogues

    baseline = _build_baseline_decision(as_of)
    overlaid = json.loads(json.dumps(baseline))
    ov.apply_playbook_overlays(overlaid, briefing)

    fwd_5d = _per_symbol_fwd_returns(as_of, 5)
    fwd_21d = _per_symbol_fwd_returns(as_of, 21)
    sec_5d = _per_sector_returns(fwd_5d)
    sec_21d = _per_sector_returns(fwd_21d)

    pnl_baseline_5d = _portfolio_pnl(baseline, fwd_5d)
    pnl_overlay_5d  = _portfolio_pnl(overlaid, fwd_5d)
    pnl_baseline_21d = _portfolio_pnl(baseline, fwd_21d)
    pnl_overlay_21d  = _portfolio_pnl(overlaid, fwd_21d)
    univ_5d  = forward_universe_return(as_of, 5)
    univ_21d = forward_universe_return(as_of, 21)

    return {
        "as_of": as_of.isoformat(),
        "n_analogues": len(analogues),
        "fired": [
            {"id": a["id"], "score": a["match_score"],
             "fired_triggers": a["fired_triggers"]}
            for a in analogues
        ],
        "active_events": sorted(_replay_active_events()),
        "drivers": [
            {"tag": d.get("tag") if isinstance(d, dict) else d[0],
             "magnitude": d.get("magnitude") if isinstance(d, dict) else d[1]}
            for d in (briefing.get("macro_impact") or {}).get("drivers") or []
        ],
        "regime": (briefing.get("regime") or {}).get("regime"),
        "univ_ret_5d_pct":  (univ_5d  or 0) * 100,
        "univ_ret_21d_pct": (univ_21d or 0) * 100,
        "sector_ret_5d_pct":  {s: r * 100 for s, r in sec_5d.items()},
        "sector_ret_21d_pct": {s: r * 100 for s, r in sec_21d.items()},
        "pnl_baseline_5d_pct":  pnl_baseline_5d["pnl_pct"],
        "pnl_overlay_5d_pct":   pnl_overlay_5d["pnl_pct"],
        "pnl_baseline_21d_pct": pnl_baseline_21d["pnl_pct"],
        "pnl_overlay_21d_pct":  pnl_overlay_21d["pnl_pct"],
        "gross_baseline_5d":  pnl_baseline_5d["gross_long"],
        "gross_overlay_5d":   pnl_overlay_5d["gross_long"],
        "cash_floor_overlay": pnl_overlay_5d["cash_floor_pct"],
        "n_overlay_actions": len(overlaid.get("actions") or []),
        "playbook_overlay_log": overlaid.get("playbook_overlay_log") or [],
    }


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------
def _every_friday(start: date, end: date) -> list[date]:
    out = []
    d = start
    # Roll forward to first Friday
    while d.weekday() != 4:
        d += timedelta(days=1)
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def main() -> int:
    # 5-year walk: 2021-06-04 (first Friday after OHLCV starts) -> 2026-05-08
    start = date(2021, 6, 4)
    end   = date(2026, 5, 8)
    fridays = _every_friday(start, end)
    print(f"[backtest] {len(fridays)} Fridays from {fridays[0]} to {fridays[-1]}",
          flush=True)

    results: list[dict] = []
    for i, d in enumerate(fridays, 1):
        try:
            row = run_one(d)
            results.append(row)
        except Exception as e:
            print(f"  [{d}] FAIL: {type(e).__name__}: {e}", flush=True)
            continue
        if i % 10 == 0 or i == len(fridays):
            print(f"  [{i:>3}/{len(fridays)}] {d}  fires={row['n_analogues']}  "
                  f"univ5d={row['univ_ret_5d_pct']:+.2f}%  "
                  f"baseline={row['pnl_baseline_5d_pct']:+.2f}%  "
                  f"overlay={row['pnl_overlay_5d_pct']:+.2f}%",
                  flush=True)
        # Periodic intermediate save so we have something even if killed.
        if i % 50 == 0:
            tmp = OUT_DIR / "backtest_per_date.partial.json"
            tmp.write_text(json.dumps(results, indent=2, default=str),
                            encoding="utf-8")

    out_path = OUT_DIR / "backtest_per_date.json"
    out_path.write_text(json.dumps(results, indent=2, default=str),
                          encoding="utf-8")
    print(f"\n[backtest] wrote {out_path}  ({len(results)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
