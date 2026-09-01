"""Intraday volume/price signals from data/intraday/marketwatch.parquet.

Honest scope note, read before using either function here: PSX MarketWatch
snapshots are captured at a handful of discrete checkpoints per trading day
(3 as of 2026-09-01: ~10:00, ~11:30, ~13:30 PKT -- see
.github/workflows/intraday_session.yml), not continuous tick data. That
means:

  - `intraday_reference_price()` is a snapshot-weighted approximation, NOT a
    true volume-weighted average price. A real VWAP needs continuous
    intraday prints; this repo doesn't capture those. Don't present this as
    VWAP to anyone who'd act on the distinction (e.g. comparing broker
    execution quality against it).
  - `detect_volume_spikes()` compares cumulative volume-to-date at snapshot
    time against a time-of-day-adjusted expectation from trailing EOD
    averages -- a heuristic, not a real intraday volume profile (which
    would need years of minute-bar history to build properly).

Both are still more signal than nothing, and cheap given the data already
flows -- just don't oversell the precision.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MARKETWATCH_PATH = ROOT / "data" / "intraday" / "marketwatch.parquet"
OHLCV_DIR = ROOT / "data" / "ohlcv"

# PSX regular session: 09:32 - 15:30 PKT (UTC+5) = 04:32 - 10:30 UTC.
SESSION_START_UTC = dtime(4, 32)
SESSION_END_UTC = dtime(10, 30)


def _session_fraction_elapsed(ts: pd.Timestamp) -> float:
    """0.0 at session open, 1.0 at session close, for a UTC timestamp."""
    t = ts.time()
    start_s = SESSION_START_UTC.hour * 3600 + SESSION_START_UTC.minute * 60
    end_s = SESSION_END_UTC.hour * 3600 + SESSION_END_UTC.minute * 60
    t_s = t.hour * 3600 + t.minute * 60 + t.second
    if t_s <= start_s:
        return 0.0
    if t_s >= end_s:
        return 1.0
    return (t_s - start_s) / (end_s - start_s)


def _load_today_snapshots(as_of_date: str | None = None) -> pd.DataFrame:
    if not MARKETWATCH_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(MARKETWATCH_PATH)
    df["snapshot_at"] = pd.to_datetime(df["snapshot_at"])
    target = as_of_date or datetime.now(timezone.utc).date().isoformat()
    return df[df["snapshot_at"].dt.date.astype(str) == target].copy()


def intraday_reference_price(symbol: str, as_of_date: str | None = None) -> dict | None:
    """Volume-weighted average of today's discrete snapshots for `symbol`.

    NOT a true VWAP -- see module docstring. Returns None if no snapshots
    exist for the symbol today.
    """
    df = _load_today_snapshots(as_of_date)
    if df.empty:
        return None
    rows = df[df["symbol"] == symbol.upper()].sort_values("snapshot_at")
    if rows.empty:
        return None
    # Volume here is cumulative-to-snapshot; use the incremental volume
    # between consecutive snapshots as the weight for that interval's price.
    vols = rows["volume"].to_numpy()
    prices = rows["current"].to_numpy()
    incremental = [vols[0]] + [max(vols[i] - vols[i - 1], 0) for i in range(1, len(vols))]
    total_vol = sum(incremental)
    if total_vol <= 0:
        return {"symbol": symbol, "n_snapshots": len(rows),
                "reference_price": float(prices[-1]), "method": "last_snapshot_fallback"}
    weighted = sum(p * v for p, v in zip(prices, incremental)) / total_vol
    return {
        "symbol": symbol,
        "n_snapshots": len(rows),
        "reference_price": round(float(weighted), 2),
        "latest_price": float(prices[-1]),
        "cumulative_volume": float(vols[-1]),
        "method": "snapshot_weighted_approximation",
    }


def detect_volume_spikes(min_ratio: float = 2.0, as_of_date: str | None = None) -> list[dict]:
    """Symbols trading unusually heavy cumulative volume for this point in
    the session, vs a time-of-day-adjusted expectation from trailing 20d
    EOD average volume. Heuristic -- see module docstring.
    """
    df = _load_today_snapshots(as_of_date)
    if df.empty:
        return []
    latest = df.sort_values("snapshot_at").groupby("symbol").last().reset_index()

    out = []
    for _, row in latest.iterrows():
        sym = row["symbol"]
        cur_vol = row.get("volume")
        if pd.isna(cur_vol) or cur_vol <= 0:
            continue
        ohlcv_path = OHLCV_DIR / f"{sym}.parquet"
        if not ohlcv_path.exists():
            continue
        try:
            hist = pd.read_parquet(ohlcv_path)
            if "volume" not in hist.columns or len(hist) < 20:
                continue
            avg_vol_20d = float(hist["volume"].tail(20).mean())
        except Exception:
            continue
        if avg_vol_20d <= 0:
            continue
        frac = _session_fraction_elapsed(row["snapshot_at"])
        frac = max(frac, 0.15)  # avoid huge ratios right at the open on thin data
        expected_by_now = avg_vol_20d * frac
        ratio = cur_vol / expected_by_now if expected_by_now > 0 else None
        if ratio is not None and ratio >= min_ratio:
            out.append({
                "symbol": sym,
                "cumulative_volume": float(cur_vol),
                "expected_by_now": round(expected_by_now, 0),
                "ratio_vs_expected": round(ratio, 2),
                "session_fraction_elapsed": round(frac, 2),
                "snapshot_at": str(row["snapshot_at"]),
                "change_pct": row.get("change_pct"),
            })
    return sorted(out, key=lambda r: -r["ratio_vs_expected"])


if __name__ == "__main__":  # pragma: no cover  (manual run)
    spikes = detect_volume_spikes()
    print(f"{len(spikes)} volume-spike candidate(s) today:")
    for s in spikes:
        print(f"  {s['symbol']}: {s['ratio_vs_expected']}x expected "
              f"(change {s['change_pct']:+.2f}%)" if s.get("change_pct") is not None
              else f"  {s['symbol']}: {s['ratio_vs_expected']}x expected")
