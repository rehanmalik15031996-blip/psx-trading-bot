"""Sector-level fundamental medians for the trading universe.

The analyst pointed out that an absolute P/E of 7.4x means nothing without
a peer benchmark — a cement name at 7.4x is cheap, a bank at 7.4x is
average. This module recomputes per-sector median P/E, P/B, dividend
yield, and payout ratio from the latest cached fundamentals snapshots
and writes the result to ``data/fundamentals/_sector_medians.json``.

It also enriches each per-symbol fundamentals parquet with two
*comparison* fields:

    pe_vs_sector_pct   — positive = expensive vs sector median
    pb_vs_sector_pct   — same convention

so downstream consumers (briefing builder, Value tab) get the comparison
without recomputing it on every render.

The module is intentionally pure-pandas, no network calls — refresh it
manually after any fundamentals refresh, or rely on the auto-refresh
hook in ``YFinanceFundamentalsConnector.fetch``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUND_DIR = PROJECT_ROOT / "data" / "fundamentals"
SECTOR_MEDIANS_PATH = FUND_DIR / "_sector_medians.json"

_RATIO_FIELDS = (
    "pe_ratio",
    "pb_ratio",
    "dividend_yield_pct",
    "payout_ratio_pct",
)


def _median(vals: Iterable[float]) -> float | None:
    """Robust median that ignores None/NaN and returns None for empty input."""
    cleaned = [float(v) for v in vals
               if v is not None and v == v]  # NaN trap
    if not cleaned:
        return None
    cleaned.sort()
    n = len(cleaned)
    mid = n // 2
    if n % 2 == 1:
        return round(cleaned[mid], 2)
    return round((cleaned[mid - 1] + cleaned[mid]) / 2.0, 2)


def _load_universe_rows() -> list[dict]:
    """Latest per-symbol fundamentals row + sector annotation."""
    import pandas as pd  # local import keeps brain free of hard deps at import time
    from config.universe import UNIVERSE

    rows: list[dict] = []
    for ent in UNIVERSE:
        p = FUND_DIR / f"{ent.symbol}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        if df.empty:
            continue
        rec = df.iloc[-1].to_dict()
        rec["_sector"] = ent.sector
        rec["_symbol"] = ent.symbol
        rows.append(rec)
    return rows


def compute_sector_medians() -> dict[str, dict]:
    """Return ``{sector: {pe_med, pb_med, yield_med, payout_med, n}}``.

    Single-stock sectors are still emitted but with ``n=1``; downstream
    consumers should treat single-stock medians cautiously.
    """
    rows = _load_universe_rows()
    by_sector: dict[str, list[dict]] = {}
    for r in rows:
        by_sector.setdefault(r["_sector"], []).append(r)

    out: dict[str, dict] = {}
    for sector, recs in by_sector.items():
        block = {
            "n": len(recs),
            "pe_med":     _median(r.get("pe_ratio") for r in recs),
            "pb_med":     _median(r.get("pb_ratio") for r in recs),
            "yield_med":  _median(r.get("dividend_yield_pct") for r in recs),
            "payout_med": _median(r.get("payout_ratio_pct") for r in recs),
            "members":    sorted(r["_symbol"] for r in recs),
        }
        out[sector] = block
    return out


def refresh_sector_medians() -> dict[str, dict]:
    """Compute, persist to JSON, and back-fill comparison fields per symbol."""
    import pandas as pd

    medians = compute_sector_medians()
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "computed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "by_sector": medians,
    }
    SECTOR_MEDIANS_PATH.write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Back-fill pe_vs_sector_pct, pb_vs_sector_pct on each per-symbol parquet
    # so renderers don't have to look up medians on every read.
    rows = _load_universe_rows()
    for r in rows:
        sector = r["_sector"]
        sym = r["_symbol"]
        block = medians.get(sector) or {}
        pe_med = block.get("pe_med")
        pb_med = block.get("pb_med")
        pe_diff = (
            (r.get("pe_ratio") / pe_med - 1.0) * 100.0
            if (r.get("pe_ratio") is not None and pe_med not in (None, 0))
            else None
        )
        pb_diff = (
            (r.get("pb_ratio") / pb_med - 1.0) * 100.0
            if (r.get("pb_ratio") is not None and pb_med not in (None, 0))
            else None
        )
        try:
            p = FUND_DIR / f"{sym}.parquet"
            df = pd.read_parquet(p)
            df.loc[df.index[-1], "sector"] = sector
            df.loc[df.index[-1], "pe_vs_sector_pct"] = (
                round(pe_diff, 1) if pe_diff is not None else None)
            df.loc[df.index[-1], "pb_vs_sector_pct"] = (
                round(pb_diff, 1) if pb_diff is not None else None)
            df.loc[df.index[-1], "sector_pe_med"] = pe_med
            df.loc[df.index[-1], "sector_pb_med"] = pb_med
            df.to_parquet(p, index=False)
        except Exception:
            # Don't crash the whole refresh on one bad parquet
            continue
    return medians


def load_sector_medians() -> dict:
    """Read the cached medians JSON. Returns ``{}`` if missing."""
    if not SECTOR_MEDIANS_PATH.exists():
        return {}
    try:
        return json.loads(SECTOR_MEDIANS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sector_for(symbol: str) -> str | None:
    """Convenience passthrough so callers don't need to know the universe shape."""
    from config.universe import sector_of
    return sector_of(symbol)


if __name__ == "__main__":  # pragma: no cover  (manual run)
    print("Refreshing sector medians...")
    out = refresh_sector_medians()
    for sec, block in sorted(out.items()):
        print(f"  {sec:<22} n={block['n']}  P/E_med={block['pe_med']!s:>6}  "
              f"P/B_med={block['pb_med']!s:>6}  Yield_med={block['yield_med']!s:>5}%  "
              f"Payout_med={block['payout_med']!s:>5}%")
    print(f"\nWritten to {SECTOR_MEDIANS_PATH}")
