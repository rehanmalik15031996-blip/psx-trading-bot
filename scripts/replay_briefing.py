"""Reconstruct a Master Strategist briefing for any historical date.

Reads on-disk parquets (OHLCV, FX, commodities) and supplements with
a small static table of publicly-known macroeconomic events (SBP
policy-rate decisions, IMF approvals, PKR shocks, circular-debt
events). The output mimics what ``brain.master_strategist.build_briefing``
would have returned on that date — enough to feed the playbook
matcher and run an honest historical test.

Honest scope (read this before trusting the output):

* **Universe = OHLCV-derived equal-weighted basket**, not the
  official KSE-100. Direction and magnitude track KSE-100 closely but
  individual % differ. Fine for matcher facts (5d / 21d / breadth).
* **Macro KPI levels (KIBOR, T-bill, FX reserves, CPI YoY)** are NOT
  replayable from on-disk data — those parquets only have ~3 rows.
  Replay returns ``None`` for them, so any case relying on those
  thresholds will silently fail to fire. This is documented in the
  test report.
* **SBP policy-rate decisions** ARE supplied via an embedded table
  of every MPC decision since Jan-2020 (public knowledge, sourced
  from sbp.org.pk press-releases). The replay sets
  ``policy_rate_pct`` and the cycle-context counters from this table.
* **FIPI flows** are not replayable; replay returns ``None``.
* **Valuation aggregates** (P/E, P/B, n_buy_value, n_quality_high)
  are not replayable without a fundamentals time-series; replay
  returns zeroed counts so cases requiring them won't fire.
* **External events** (IMF approvals, PKR shocks, circular debt) are
  injected from a small static table so cases referencing them via
  ``event:<key>`` can fire correctly.

Anything missing from this list is expected to show up as a "gap"
when the test runs.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OHLCV_DIR = ROOT / "data" / "ohlcv"
MACRO_DIR = ROOT / "data" / "macro"


# ---------------------------------------------------------------------------
# Public-knowledge macro tables
# ---------------------------------------------------------------------------
# SBP MPC decisions since January 2020. Source: SBP press releases on
# sbp.org.pk. ONLY decisions that changed the rate (a "hold" decision
# isn't an event for our purposes).
SBP_DECISIONS: list[tuple[str, float]] = [
    ("2020-01-28", 13.25),
    ("2020-03-17", 12.50),  # COVID emergency cut #1
    ("2020-03-24", 11.00),  # COVID emergency cut #2
    ("2020-04-16", 9.00),   # COVID emergency cut #3
    ("2020-05-15", 8.00),
    ("2020-06-25", 7.00),
    ("2021-09-20", 7.25),   # First post-COVID hike
    ("2021-11-19", 8.75),
    ("2021-12-14", 9.75),
    ("2022-04-07", 12.25),  # Emergency 250bp hike (PKR/IMF stress)
    ("2022-05-23", 13.75),
    ("2022-07-07", 15.00),
    ("2022-11-25", 16.00),
    ("2023-01-23", 17.00),
    ("2023-03-02", 20.00),  # Emergency 300bp hike
    ("2023-04-04", 21.00),
    ("2023-06-26", 22.00),  # Cycle peak
    ("2024-06-10", 20.50),  # CYCLE PIVOT — first cut
    ("2024-07-29", 19.50),
    ("2024-09-12", 17.50),
    ("2024-11-04", 15.00),
    ("2024-12-16", 13.00),
    ("2025-01-27", 12.00),
    ("2025-05-05", 11.00),
    ("2025-06-16", 11.00),  # hold
    ("2025-12-15", 11.50),  # 50bp hike — circular-debt wash-up cost
]

# Major external events with manually-curated decay windows. Each
# entry becomes an "active event" in the briefing for the configured
# decay_days after its date.
HISTORICAL_EVENTS: list[dict] = [
    # IMF program approvals (initial)
    {"date": "2023-07-12", "key": "imf_sba_or_eff_approval", "decay_days": 30,
     "description": "IMF $3bn 9-month SBA approved."},
    {"date": "2024-09-25", "key": "imf_sba_or_eff_approval", "decay_days": 45,
     "description": "IMF $7bn 37-month EFF approved."},
    # IMF reviews / staff-level agreements / disbursements
    # (also bullish, distinct from the initial approval). The market
    # reacts on the SLA date — board approval is a formality 4-6 weeks
    # later that adds little new information.
    # IMF review SLAs — decay shortened from 21 -> 5 days (Tier-0 patch
    # 2026-05-03). Per the case docstring, the 5-day reaction is the
    # only reliably positive window; a 21-day decay caused 18 fires
    # over the 24-month replay with a 5.6% hit rate (the early bullish
    # print routinely faded as "what next" macro drivers re-asserted).
    # Aligns with the playbook's own guidance: "Do not extend the
    # trade past 10 trading days."
    {"date": "2023-10-11", "key": "imf_review_completed", "decay_days": 5,
     "description": "IMF first SBA review SLA (board approval Nov-15) — $700m tranche."},
    {"date": "2024-03-20", "key": "imf_review_completed", "decay_days": 5,
     "description": "IMF SBA second review SLA (board approval 29-Apr) — $1.1bn tranche."},
    {"date": "2025-03-25", "key": "imf_review_completed", "decay_days": 5,
     "description": "IMF EFF first review SLA ($1bn tranche)."},
    {"date": "2025-09-25", "key": "imf_review_completed", "decay_days": 5,
     "description": "IMF EFF second review SLA."},
    # Political
    {"date": "2024-02-08", "key": "election_window", "decay_days": 14,
     "description": "Pakistan general election."},
    # Structural — decay shortened from 60 -> 7 days (Tier-0 patch
    # 2026-05-03). The case `circular_debt_resolution_large` is a
    # 2-5 day "buy-the-news" trigger, not a 60-day standing call;
    # leaving the event active for 60 days re-fired the case every
    # Mon/Wed/Fri (~27 fires per event) and turned a 76% precision
    # case into a 32% precision case. The case is still expected to
    # be HELD 30-45 days per its `playbook` text — that's the trader's
    # holding window, not the system's re-firing window.
    {"date": "2025-12-15", "key": "circular_debt_resolution_event", "decay_days": 7,
     "description": "Rs 1.225 trn power circular-debt clearance."},
    # FX shocks
    {"date": "2023-01-26", "key": "pkr_devaluation_event", "decay_days": 21,
     "description": "Interbank PKR cap removed; ~10% drop in 3 days."},
    # Commodity shocks
    {"date": "2022-02-24", "key": "brent_shock_event", "decay_days": 21,
     "description": "Russia invades Ukraine; Brent +28% in 21d."},
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _pct_change(series: pd.Series, n: int) -> float | None:
    if len(series) <= n:
        return None
    last = float(series.iloc[-1])
    prev = float(series.iloc[-n - 1])
    if prev == 0 or pd.isna(prev) or pd.isna(last):
        return None
    return last / prev - 1.0


def _level_at(parquet: Path, as_of: date, value_col: str = "value") -> tuple[float | None, dict]:
    """Return (level_at_or_before_as_of, {ret_5d, ret_21d}). Skips
    weekends silently."""
    if not parquet.exists():
        return None, {}
    df = pd.read_parquet(parquet)
    if "date" not in df.columns or value_col not in df.columns:
        return None, {}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["date"] <= as_of].sort_values("date")
    if df.empty:
        return None, {}
    s = df[value_col].astype(float)
    return float(s.iloc[-1]), {
        "ret_5d":  _pct_change(s, 5),
        "ret_21d": _pct_change(s, 21),
    }


def _kpi_at(as_of: date) -> dict:
    """Read macro KPI levels at ``as_of`` from the now-deep parquets
    written by ``scripts/ingest_macro_history.py``.

    Returns a flat dict mirroring the shape ``industry_kpis.kpis``
    expects (the playbook _macro_level_facts converter looks here).
    Missing parquets / dates ⇒ all-None (matcher tolerates None)."""
    kpis = {
        "policy_rate_pct": None, "kibor_3m_pct": None,
        "tbill_3m_pct": None,    "cpi_yoy_pct": None,
        "reserves_total_usd_mn": None,
        "kse100_ret_5d": None,   "kse100_ret_21d": None,
    }
    sbp_path = MACRO_DIR / "sbp_rates.parquet"
    if sbp_path.exists():
        df = pd.read_parquet(sbp_path)
        if "date" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df[df["date"] <= as_of].sort_values("date")
            if not df.empty:
                last = df.iloc[-1]
                for src, dst in (
                    ("policy_rate_pct",       "policy_rate_pct"),
                    ("kibor_3m_pct",          "kibor_3m_pct"),
                    ("tbill_3m_pct",          "tbill_3m_pct"),
                    ("reserves_total_usd_mn", "reserves_total_usd_mn"),
                ):
                    v = last.get(src) if src in df.columns else None
                    if v is not None and not pd.isna(v):
                        try:
                            kpis[dst] = float(v)
                        except (TypeError, ValueError):
                            pass
    cpi_path = MACRO_DIR / "cpi_pakistan.parquet"
    if cpi_path.exists():
        df = pd.read_parquet(cpi_path)
        if "date" in df.columns and "cpi_yoy_pct" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df[df["date"] <= as_of].sort_values("date")
            if not df.empty:
                v = df.iloc[-1]["cpi_yoy_pct"]
                if v is not None and not pd.isna(v):
                    try:
                        kpis["cpi_yoy_pct"] = float(v)
                    except (TypeError, ValueError):
                        pass
    kse_path = MACRO_DIR / "kse100.parquet"
    if kse_path.exists():
        df = pd.read_parquet(kse_path)
        if "date" in df.columns and "kse100_close" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df[df["date"] <= as_of].sort_values("date")
            # Freshness gate (Tier-1 fix 2026-05-03). The on-disk
            # kse100 parquet currently has a 2021-09 -> 2026-04 gap;
            # without this check, every replay date in that window
            # gets the same stale -6% return clamped to 2021-09-30.
            # When the latest in-window row is more than 7 calendar
            # days behind as_of, drop the parquet and let the
            # universe-proxy fallback in replay_briefing populate
            # ret_5d / ret_21d from the OHLCV directory instead.
            if len(df) >= 22:
                latest_d = df["date"].iloc[-1]
                age_days = (as_of - latest_d).days
                if age_days <= 7:
                    s = df["kse100_close"].astype(float)
                    kpis["kse100_ret_5d"]  = _pct_change(s, 5)
                    kpis["kse100_ret_21d"] = _pct_change(s, 21)
    return kpis


# ---------------------------------------------------------------------------
# Universe-derived facts (proxy for KSE-100 / breadth)
# ---------------------------------------------------------------------------
@dataclass
class UniverseSnapshot:
    n_symbols: int
    universe_close_avg: float | None
    ret_5d: float | None
    ret_21d: float | None
    ret_150d: float | None
    breadth_pct_up_5d: float | None
    market_risk_on: bool | None


def _universe_snapshot(as_of: date,
                        symbols: Iterable[str] | None = None) -> UniverseSnapshot:
    files = sorted(OHLCV_DIR.glob("*.parquet"))
    if symbols is not None:
        wanted = {s.upper() for s in symbols}
        files = [f for f in files if f.stem.upper() in wanted]
    closes: dict[str, pd.Series] = {}
    for f in files:
        df = pd.read_parquet(f)
        if "close" not in df.columns or "date" not in df.columns:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[df["date"] <= as_of].sort_values("date")
        if len(df) < 22:
            continue
        closes[f.stem] = df.set_index("date")["close"].astype(float)
    if not closes:
        return UniverseSnapshot(0, None, None, None, None, None, None)

    rets_5d, rets_21d, rets_150d = [], [], []
    for s in closes.values():
        if len(s) >= 6 and s.iloc[-6]:
            rets_5d.append(s.iloc[-1] / s.iloc[-6] - 1.0)
        if len(s) >= 22 and s.iloc[-22]:
            rets_21d.append(s.iloc[-1] / s.iloc[-22] - 1.0)
        if len(s) >= 151 and s.iloc[-151]:
            rets_150d.append(s.iloc[-1] / s.iloc[-151] - 1.0)
    universe_avg_close = sum(s.iloc[-1] for s in closes.values()) / len(closes)
    ret_5d = sum(rets_5d) / len(rets_5d) if rets_5d else None
    ret_21d = sum(rets_21d) / len(rets_21d) if rets_21d else None
    ret_150d = sum(rets_150d) / len(rets_150d) if rets_150d else None
    breadth_up_5d = (100.0 * sum(1 for r in rets_5d if r > 0) / len(rets_5d)
                      if rets_5d else None)
    market_risk_on = None if ret_150d is None else bool(ret_150d > 0)
    return UniverseSnapshot(len(closes), universe_avg_close,
                              ret_5d, ret_21d, ret_150d,
                              breadth_up_5d, market_risk_on)


# ---------------------------------------------------------------------------
# Active SBP rate / cycle context for `as_of`
# ---------------------------------------------------------------------------
def _policy_rate_at(as_of: date) -> float | None:
    last = None
    for d, r in SBP_DECISIONS:
        if datetime.strptime(d, "%Y-%m-%d").date() <= as_of:
            last = r
        else:
            break
    return last


def _drivers_from_sbp(as_of: date,
                       lookback_days: int = 21) -> list[dict]:
    """Emit driver:rate_up / driver:rate_down tags when an SBP decision
    in the trailing `lookback_days` changed the rate."""
    drivers: list[dict] = []
    cutoff = as_of - timedelta(days=lookback_days)
    for i in range(1, len(SBP_DECISIONS)):
        d_str, r = SBP_DECISIONS[i]
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        if d > as_of or d < cutoff:
            continue
        prev_r = SBP_DECISIONS[i - 1][1]
        delta = r - prev_r
        if delta <= -0.25:
            drivers.append({"tag": "rate_down",
                              "magnitude": "STRONG" if delta <= -1.0 else "MODERATE"})
        elif delta >= 0.25:
            drivers.append({"tag": "rate_up",
                              "magnitude": "STRONG" if delta >= 1.0 else "MODERATE"})
    return drivers


def _drivers_from_macro(as_of: date) -> list[dict]:
    """Emit pkr_weak / pkr_strong / oil_up / oil_down tags from
    USD/PKR and Brent moves over the trailing 21d."""
    drivers: list[dict] = []
    fx, fx_meta = _level_at(MACRO_DIR / "usdpkr.parquet", as_of)
    if fx is not None and fx_meta.get("ret_21d") is not None:
        r = fx_meta["ret_21d"]
        if r >= 0.05:
            drivers.append({"tag": "pkr_weak",
                              "magnitude": "STRONG" if r >= 0.10 else "MODERATE"})
        elif r <= -0.03:
            drivers.append({"tag": "pkr_strong",
                              "magnitude": "MODERATE"})
    br, br_meta = _level_at(MACRO_DIR / "brent.parquet", as_of)
    if br is not None and br_meta.get("ret_21d") is not None:
        r = br_meta["ret_21d"]
        if r >= 0.10:
            drivers.append({"tag": "oil_up",
                              "magnitude": "STRONG" if r >= 0.20 else "MODERATE"})
        elif r <= -0.10:
            drivers.append({"tag": "oil_down",
                              "magnitude": "STRONG" if r <= -0.20 else "MODERATE"})
    return drivers


def _events_active_on(as_of: date) -> list[dict]:
    out: list[dict] = []
    for ev in HISTORICAL_EVENTS:
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        if d > as_of:
            continue
        decay = int(ev.get("decay_days") or 14)
        if (as_of - d).days <= decay:
            out.append({**ev})
    return out


# ---------------------------------------------------------------------------
# Forward returns (for the "did the case predict correctly?" check)
# ---------------------------------------------------------------------------
def forward_universe_return(as_of: date,
                              days: int) -> float | None:
    """Average forward universe return over `days` trading days from
    `as_of`. Skips weekends (uses calendar days * 1.4 buffer to find
    enough rows)."""
    files = sorted(OHLCV_DIR.glob("*.parquet"))
    rs: list[float] = []
    for f in files:
        df = pd.read_parquet(f)
        if "close" not in df.columns or "date" not in df.columns:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date")
        anchor = df[df["date"] <= as_of]
        future = df[df["date"] > as_of]
        if anchor.empty or len(future) < days:
            continue
        c0 = float(anchor.iloc[-1]["close"])
        c1 = float(future.iloc[days - 1]["close"])
        if c0 > 0:
            rs.append(c1 / c0 - 1.0)
    if not rs:
        return None
    return sum(rs) / len(rs)


def forward_symbol_return(symbol: str, as_of: date,
                            days: int) -> float | None:
    f = OHLCV_DIR / f"{symbol.upper()}.parquet"
    if not f.exists():
        return None
    df = pd.read_parquet(f)
    if "close" not in df.columns or "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    anchor = df[df["date"] <= as_of]
    future = df[df["date"] > as_of]
    if anchor.empty or len(future) < days:
        return None
    c0 = float(anchor.iloc[-1]["close"])
    c1 = float(future.iloc[days - 1]["close"])
    if c0 <= 0:
        return None
    return c1 / c0 - 1.0


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def _cycle_override_for(as_of: date) -> dict:
    """Pre-compute the playbook _cycle_context for `as_of`. Prefers the
    on-disk ``_policy_rate_history.json`` (now backfilled to 5 years
    by ``scripts/ingest_macro_history.py``) and falls back to the
    embedded SBP_DECISIONS list for environments where the script
    hasn't been run yet."""
    from brain import playbook as pb
    history_path = MACRO_DIR / "_policy_rate_history.json"
    history: list[tuple[date, float]] = []
    if history_path.exists():
        try:
            payload = json.loads(history_path.read_text(encoding="utf-8"))
            entries = payload if isinstance(payload, list) else (
                payload.get("entries") or payload.get("history") or [])
            for e in entries:
                try:
                    d = datetime.strptime(str(e.get("date")), "%Y-%m-%d").date()
                    r = float(e.get("rate_pct"))
                except (TypeError, ValueError):
                    continue
                history.append((d, r))
        except (json.JSONDecodeError, OSError):
            history = []
    # Need at least the same depth as the curated list -- if the JSON
    # has only the 4-day "today" snapshot we fall back.
    if len(history) < 10:
        history = [(datetime.strptime(d, "%Y-%m-%d").date(), r)
                    for d, r in SBP_DECISIONS]
    return pb._cycle_context(as_of=as_of, history_entries=history)


def replay_briefing(as_of: date) -> dict:
    """Build a briefing dict for `as_of`. Compatible with the keys the
    playbook matcher reads."""
    snap = _universe_snapshot(as_of)
    fx, _ = _level_at(MACRO_DIR / "usdpkr.parquet", as_of)
    br, _ = _level_at(MACRO_DIR / "brent.parquet", as_of)
    gd, _ = _level_at(MACRO_DIR / "gold.parquet", as_of)
    policy = _policy_rate_at(as_of)
    drivers = _drivers_from_sbp(as_of) + _drivers_from_macro(as_of)
    # Add a circular-debt driver alongside the historical event for Dec-2025.
    if any(e.get("key") == "circular_debt_resolution_event"
           for e in _events_active_on(as_of)):
        drivers.append({"tag": "circular_debt_resolution", "magnitude": "STRONG"})
    events = _events_active_on(as_of)
    cycle_override = _cycle_override_for(as_of)

    # Macro KPI levels from the now-deep parquets (5 years backfilled
    # by scripts/ingest_macro_history.py). When the on-disk KSE-100
    # parquet doesn't cover this date, fall back to the universe proxy.
    kpis = _kpi_at(as_of)
    if kpis.get("kse100_ret_5d") is None:
        kpis["kse100_ret_5d"]  = snap.ret_5d
    if kpis.get("kse100_ret_21d") is None:
        kpis["kse100_ret_21d"] = snap.ret_21d

    # Mutual-fund flows for `as_of` (best-effort -- parquets are sparse
    # historically but the matcher tolerates None / empty).
    try:
        from brain import mf_flows
        mf_payload = mf_flows.universe_summary(as_of=as_of)
        # Per-stock signals -- the playbook's MF triggers expect them
        # under ``per_stock_signals``. We hydrate symbols that have
        # data in the latest holdings month plus the top accumulated /
        # distributed names so per-stock triggers can fire.
        candidate_syms: set[str] = set()
        for it in (mf_payload.get("top_accumulated_180d") or []):
            candidate_syms.add(str(it.get("symbol")).upper())
        for it in (mf_payload.get("top_distributed_180d") or []):
            candidate_syms.add(str(it.get("symbol")).upper())
        for it in (mf_payload.get("top_accumulated_30d") or []):
            candidate_syms.add(str(it.get("symbol")).upper())
        for it in (mf_payload.get("top_distributed_30d") or []):
            candidate_syms.add(str(it.get("symbol")).upper())
        # Also include symbols held in the latest available holdings
        # month so per-stock triggers see breadth, not just the top-10.
        try:
            holdings = mf_flows._load_holdings()  # noqa: SLF001
            if holdings is not None and not holdings.empty:
                hd = holdings[holdings["as_of_month"].dt.date <= as_of]
                if not hd.empty:
                    latest_m = hd["as_of_month"].max()
                    for sym in (hd[hd["as_of_month"] == latest_m]["symbol"]
                                .dropna().unique()):
                        candidate_syms.add(str(sym).upper())
        except Exception:
            pass
        per_stock = {}
        for sym in candidate_syms:
            if not sym or sym in ("NONE", "NAN"):
                continue
            try:
                sig = mf_flows.signals_for(sym, as_of=as_of)
                # Skip empty signals to keep payload small
                if any(v not in (None, "") for k, v in sig.items()
                        if k.startswith("mf_") and k != "mf_data_freshness_days"):
                    per_stock[sym] = sig
            except Exception:
                continue
        if per_stock:
            mf_payload["per_stock_signals"] = per_stock
    except Exception:
        mf_payload = {}

    # Volume confirmation signals (Tier-1 patch 2026-05-03). The
    # production builder uses ranked_top + selected from Phase-1; in
    # the replay we don't have those rankings yet, so we fan out across
    # every symbol that has an OHLCV parquet (the same universe the
    # snapshot above is built from). That gives the universe-level
    # breakout count the playbook trigger keys off.
    try:
        from brain import volume_signals
        vol_syms = sorted(p.stem for p in OHLCV_DIR.glob("*.parquet"))
        volume_payload = volume_signals.universe_summary(
            vol_syms, as_of=as_of)
        # Strip the per-stock dict to keep the briefing payload small;
        # the trigger evaluator only reads the universe-level fields.
        volume_payload.pop("per_stock", None)
    except Exception as e:
        volume_payload = {"error": f"{type(e).__name__}: {e}"}

    return {
        "as_of": as_of.isoformat(),
        "_replay": True,
        "regime": {
            "regime": ("CRISIS" if (snap.ret_5d or 0) < -0.07 else
                        "CAUTION" if (snap.ret_5d or 0) < -0.02 else "NORMAL"),
            "exposure_multiplier": 1.0,
            "universe_ret_5d":  snap.ret_5d,
            "universe_ret_21d": snap.ret_21d,
            "breadth_pct_up":   snap.breadth_pct_up_5d,
        },
        "strategy_signal": {
            "market_risk_on": snap.market_risk_on,
            "selected": [], "as_of": as_of.isoformat(),
        },
        "macro_impact": {"drivers": drivers},
        "policy_rate": {"policy_rate_pct": policy},
        "industry_kpis": {"kpis": kpis},
        "macro_snapshot": {"indicators": {
            "usdpkr": {"last": fx},
            "brent":  {"last": br},
            "gold":   {"last": gd},
        }},
        "fipi_flows": {"net_5d_pkr_mn": None},   # not replayable historically
        "scored_sentiment": {"tilt": None},
        "earnings_calendar": {"blackout_now": []},
        "value_book": {"rows": []},
        "quality_book": {"rows": []},
        "earnings_momentum": {"rows": []},
        "predictions": {"predictions": []},
        "short_candidates": {"candidates": []},
        "top_buys": {"ideas": []},
        "portfolio": {"positions": []},
        "mf_holdings": mf_payload,
        "volume_signals": volume_payload,
        "_replay_events": events,
        "_cycle_override": cycle_override,
        "_replay_universe": {
            "n_symbols": snap.n_symbols,
            "ret_150d": snap.ret_150d,
            "market_risk_on": snap.market_risk_on,
        },
    }


def install_historical_events_into_playbook() -> None:
    """Patch brain.playbook._load_active_events at runtime so the
    matcher sees our HISTORICAL_EVENTS instead of the live
    _events.json. Used by the replay test."""
    from brain import playbook as pb
    original = pb._load_active_events

    def _replay_events_loader(path: Path | None = None) -> set[str]:
        # Find which "as_of" we're replaying by looking at the live
        # file ALSO (so it doesn't break a normal call). For test
        # replay we set a sentinel via env var.
        return original(path)

    # The cleanest pattern: just leave the live events loader; our
    # replay sets active events via the briefing-level _replay_events
    # field, and we adapt the matcher with a small monkey-patch in
    # the test harness instead. Keep this function as a marker.
    _ = _replay_events_loader


if __name__ == "__main__":  # pragma: no cover
    import sys
    as_of = (datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
             if len(sys.argv) > 1 else date.today())
    b = replay_briefing(as_of)
    print(f"Replay briefing for {as_of}:")
    print(f"  Universe: n={b['_replay_universe']['n_symbols']}  "
          f"5d={b['regime']['universe_ret_5d']}  "
          f"21d={b['regime']['universe_ret_21d']}  "
          f"breadth={b['regime']['breadth_pct_up']}  "
          f"risk_on={b['_replay_universe']['market_risk_on']}")
    print(f"  Macro:    USDPKR={b['macro_snapshot']['indicators']['usdpkr']['last']}  "
          f"Brent={b['macro_snapshot']['indicators']['brent']['last']}  "
          f"Policy={b['policy_rate']['policy_rate_pct']}")
    print(f"  Drivers:  {b['macro_impact']['drivers']}")
    print(f"  Events:   {[e['key'] for e in b['_replay_events']]}")
