"""Gap-3: out-of-sample test for the three Phase F playbook cases
authored on the evening of 2026-05-14 with hindsight of that day.

The cases are:
  - distribution_day_signature   (kse100 intraday range + close-in-range)
  - event_eve_distribution       (days_to_active_event + open_to_close)
  - brent_plateau_e_and_p_decay  (brent_5d_slope + brent level)

For each PSX trading day in March-April 2026 (40 sessions BEFORE Phase F
was authored), reconstruct the facts the cases need (KSE-100 OHLC,
Brent recent series, days_to_next_event), then evaluate each trigger
against those facts.

We do NOT use the full briefing replay (which lacks intraday facts) —
we go directly to the parquets.

For each fire, we also pull the NEXT TRADING DAY's KSE-100 return to
check whether the bearish bias the case is supposed to encode actually
materialized.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OHLCV = ROOT / "data" / "ohlcv"
MACRO = ROOT / "data" / "macro"

# Events file
EVENTS_FILE = ROOT / "data" / "playbook" / "_events.json"


def _load_kse100() -> pd.DataFrame:
    """Load KSE-100 daily OHLC. Try direct file, else synth from
    a universe-equal-weight proxy."""
    candidates = [
        MACRO / "kse100.parquet",
        MACRO / "psx_index.parquet",
        ROOT / "data" / "kse100.parquet",
    ]
    for c in candidates:
        if c.exists():
            df = pd.read_parquet(c)
            df["date"] = pd.to_datetime(df["date"]).dt.date
            return df.sort_values("date").reset_index(drop=True)
    raise FileNotFoundError("No KSE-100 parquet found at " +
                             ", ".join(str(c) for c in candidates))


def _load_brent() -> pd.DataFrame:
    df = pd.read_parquet(MACRO / "brent.parquet")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _load_events() -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    raw = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw.get("events") or raw.get("items") or []
    return raw if isinstance(raw, list) else []


def _days_to_next_event(as_of: date, events: list[dict]) -> int | None:
    """Mirror brain.master_strategist._days_to_next_event:
      - 0 if inside an active event window
      - else positive days to nearest upcoming event
      - None if no future event found
    """
    best: int | None = None
    for ev in events:
        d_str = ev.get("date")
        if not d_str:
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        decay = int(ev.get("decay_days") or 14)
        if d <= as_of and (as_of - d).days <= decay:
            return 0   # inside active window
        if d > as_of:
            delta = (d - as_of).days
            if best is None or delta < best:
                best = delta
    return best


def _phase_f_facts(as_of: date, kse: pd.DataFrame,
                    brent: pd.DataFrame,
                    events: list[dict]) -> dict | None:
    """Build the Phase F facts for `as_of`. Returns None if not enough
    data (e.g. weekend or insufficient history)."""
    krow = kse[kse["date"] == as_of]
    if krow.empty:
        return None
    # We need yesterday's close to compute open-to-close (using
    # yest_close as today_open proxy, matching master_strategist).
    prior = kse[kse["date"] < as_of].tail(1)
    if prior.empty:
        return None

    # Column names vary across parquets (close vs kse100_close).
    def _pick(df: pd.DataFrame, names: list[str]) -> float | None:
        for n in names:
            if n in df.columns:
                v = df.iloc[0][n]
                try:
                    return float(v) if pd.notna(v) else None
                except (TypeError, ValueError):
                    return None
        return None

    today_close = _pick(krow, ["close", "kse100_close"])
    today_high  = _pick(krow, ["high",  "kse100_high"])
    today_low   = _pick(krow, ["low",   "kse100_low"])
    yest_close  = _pick(prior, ["close", "kse100_close"])
    if any(v is None for v in (today_close, today_high, today_low, yest_close)):
        return None

    today_open = yest_close   # proxy
    intraday_range_pct = (today_high - today_low) / today_low * 100
    close_in_range_pct = (
        (today_close - today_low) / (today_high - today_low) * 100
        if today_high > today_low else 50.0
    )
    open_to_close_pct  = (today_close - today_open) / today_open * 100

    # Brent 5d slope: (today close - close 5d ago) / close 5d ago * 100
    br_recent = brent[brent["date"] <= as_of].tail(6)
    if len(br_recent) < 6:
        brent_5d_slope = None
        brent_level = None
    else:
        # Find a close column
        close_col = next((c for c in ["close", "brent_close", "value"]
                          if c in br_recent.columns), None)
        if close_col is None:
            brent_5d_slope = None
            brent_level = None
        else:
            five_days_ago = float(br_recent.iloc[0][close_col])
            today_brent   = float(br_recent.iloc[-1][close_col])
            brent_5d_slope = (today_brent - five_days_ago) / five_days_ago * 100
            brent_level = today_brent

    return {
        "as_of": as_of.isoformat(),
        "kse100_close_in_range_pct": round(close_in_range_pct, 2),
        "kse100_intraday_range_pct": round(intraday_range_pct, 3),
        "kse100_open_to_close_pct":  round(open_to_close_pct, 3),
        "brent_5d_slope_pct":  (round(brent_5d_slope, 3)
                                if brent_5d_slope is not None else None),
        "brent_usd_bbl":       (round(brent_level, 2)
                                if brent_level is not None else None),
        "days_to_next_event":  _days_to_next_event(as_of, events),
    }


# ---------------------------------------------------------------------------
#  Trigger predicates (mirror the case definitions; no need for the full
#  playbook engine here).
# ---------------------------------------------------------------------------
def _fires_distribution_day(f: dict) -> bool:
    # cases.json: kse100_intraday_range_gte:0.8 AND kse100_close_in_range_lte:25
    return (f["kse100_intraday_range_pct"] >= 0.8 and
            f["kse100_close_in_range_pct"] <= 25.0)


def _fires_event_eve_distribution(f: dict) -> bool:
    # cases.json: days_to_active_event_lte:2 AND kse100_open_to_close_lte:-0.3
    d = f["days_to_next_event"]
    return (d is not None and d <= 2 and
            f["kse100_open_to_close_pct"] <= -0.3)


def _fires_brent_plateau(f: dict) -> bool:
    # cases.json: brent_5d_slope_lte:1.0 AND brent_gte:100
    if f["brent_5d_slope_pct"] is None or f["brent_usd_bbl"] is None:
        return False
    return f["brent_5d_slope_pct"] <= 1.0 and f["brent_usd_bbl"] >= 100.0


def _trading_days(kse: pd.DataFrame, start: date, end: date) -> list[date]:
    return [d for d in kse["date"].tolist() if start <= d <= end]


def _next_day_ret(kse: pd.DataFrame, d: date) -> float | None:
    """Return % move on the NEXT KSE-100 trading day."""
    later = kse[kse["date"] > d]
    if later.empty:
        return None
    nxt = later.iloc[0]
    close_col = next((c for c in ["close", "kse100_close"]
                      if c in kse.columns), None)
    if close_col is None:
        return None
    today = kse[kse["date"] == d]
    if today.empty:
        return None
    t0 = float(today.iloc[0][close_col])
    t1 = float(nxt[close_col])
    return (t1 - t0) / t0 * 100


def main() -> int:
    kse = _load_kse100()
    brent = _load_brent()
    events = _load_events()

    # OOS window: March-April 2026 (40 trading days BEFORE Phase F
    # cases were authored). Also include Feb 2026 for a deeper sample.
    start = date(2026, 2, 1)
    end   = date(2026, 5, 13)   # day before Phase F was authored
    sessions = _trading_days(kse, start, end)
    print(f"OOS window {start} .. {end} -> {len(sessions)} PSX sessions")

    results: list[dict] = []
    fires_by_case: dict[str, list[dict]] = {
        "distribution_day_signature": [],
        "event_eve_distribution": [],
        "brent_plateau_e_and_p_decay": [],
    }
    for s in sessions:
        f = _phase_f_facts(s, kse, brent, events)
        if f is None:
            continue
        nxt = _next_day_ret(kse, s)
        f["next_day_pct"] = (round(nxt, 3) if nxt is not None else None)
        f["fires"] = {}
        for case_id, pred in (
            ("distribution_day_signature", _fires_distribution_day),
            ("event_eve_distribution", _fires_event_eve_distribution),
            ("brent_plateau_e_and_p_decay", _fires_brent_plateau),
        ):
            fired = pred(f)
            f["fires"][case_id] = fired
            if fired:
                fires_by_case[case_id].append(f)
        results.append(f)

    print(f"Evaluated {len(results)} sessions with full facts")
    print()
    for case_id, fires in fires_by_case.items():
        n_fires = len(fires)
        rate    = n_fires / max(len(results), 1)
        next_returns = [f["next_day_pct"] for f in fires
                         if f["next_day_pct"] is not None]
        if next_returns:
            mean_nxt = sum(next_returns) / len(next_returns)
            n_down   = sum(1 for r in next_returns if r < 0)
            hit_rate = n_down / len(next_returns)
        else:
            mean_nxt, n_down, hit_rate = 0, 0, 0
        # Bear bias: each case is supposed to encode "tomorrow likely
        # down" — so a HIT is next_day_pct < 0.
        print(f"  {case_id:<32s} fires={n_fires:>3} "
              f"({rate*100:>5.1f}% of days)   "
              f"next-day mean={mean_nxt:+.3f}%   "
              f"down-rate={hit_rate*100:>5.1f}%   "
              f"hits={n_down}/{len(next_returns)}")

    print()
    print("=" * 88)
    print("VERDICT")
    print("=" * 88)

    def _verdict(name: str, fires: list[dict]):
        next_returns = [f["next_day_pct"] for f in fires
                         if f["next_day_pct"] is not None]
        n = len(next_returns)
        if n == 0:
            print(f"  {name:<32s} [SILENT] never fired in OOS window — "
                  "either too specific (good) or set too tight (bad).")
            return
        rate = n / max(len(results), 1)
        if rate > 0.30:
            print(f"  {name:<32s} [TOO LOOSE] fires {rate*100:.0f}% of "
                  "OOS days. Threshold needs tightening.")
        mean_nxt = sum(next_returns) / n
        n_down = sum(1 for r in next_returns if r < 0)
        hit = n_down / n
        if hit >= 0.55 and mean_nxt < 0:
            print(f"  {name:<32s} [VALID] {hit*100:.0f}% of fires "
                  f"correctly preceded a down session. mean next-day "
                  f"{mean_nxt:+.2f}%. Case generalizes.")
        elif hit >= 0.45 and abs(mean_nxt) < 0.3:
            print(f"  {name:<32s} [WEAK] {hit*100:.0f}% hit / "
                  f"{mean_nxt:+.2f}% next-day — barely better than coin flip.")
        else:
            print(f"  {name:<32s} [HINDSIGHT] {hit*100:.0f}% hit / "
                  f"{mean_nxt:+.2f}% next-day. The case does NOT predict "
                  "tomorrow's direction OOS. This is curve-fit to May-14.")

    for name, fires in fires_by_case.items():
        _verdict(name, fires)

    out = ROOT / "data" / "_research" / "phase_f_oos_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "window": [str(start), str(end)],
        "n_sessions": len(results),
        "fires_by_case": {k: [{"as_of": f["as_of"],
                                 "next_day_pct": f["next_day_pct"]}
                                for f in v]
                            for k, v in fires_by_case.items()},
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
