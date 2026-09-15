"""Storage helpers — Parquet read/write for OHLCV and daily snapshots.

Directory layout:
    data/ohlcv/{SYMBOL}.parquet      — one file per symbol, cumulative daily bars
    data/snapshots/{DATE}.json       — one file per trading day, full connector output

Keeping per-symbol Parquet means incremental appends are fast and you can
inspect any single name with pandas in 2 lines.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent
OHLCV_DIR = ROOT / "ohlcv"
SNAPSHOT_DIR = ROOT / "snapshots"
OHLCV_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def atomic_to_parquet(df: pd.DataFrame, path: Path, **kwargs) -> None:
    """Write `df` to `path` without ever leaving a truncated/corrupt file
    behind if the process is killed mid-write.

    Found 2026-09-15: `data/news/scored_news.parquet` was silently corrupt
    (unreadable, "Couldn't deserialize thrift") from 2026-09-01 onward,
    blocking news_scoring's health status for 14 days straight with no
    error surfaced anywhere -- df.to_parquet() writing directly to the
    final path isn't atomic, so an interrupted write (scheduled task
    killed, machine slept mid-run, disk full) leaves corrupt bytes at the
    real path and every subsequent read of it raises. Writing to a temp
    file in the same directory (so the final os.replace is on the same
    filesystem, hence atomic) and renaming over the target means a reader
    always sees either the old complete file or the new complete file,
    never a partial one.

    A grep across the repo at the time found 26 files writing parquet
    directly without this pattern -- this fixes the ones on the hot,
    unattended-scheduled path (this module, news scoring, live market,
    dividend calendar, Google Trends); the rest are lower-frequency and
    were left as-is, but the same fix applies if one of them corrupts too.
    """
    path = Path(path)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        df.to_parquet(tmp_path, **kwargs)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def ohlcv_path(symbol: str) -> Path:
    return OHLCV_DIR / f"{symbol.upper()}.parquet"


def save_ohlcv(symbol: str, rows: Iterable[dict]) -> int:
    """Overwrite the Parquet file for `symbol` with the given rows.

    Returns number of rows written. Rows must contain at least
    ['date', 'symbol', 'open', 'close', 'volume']. Date is parsed and
    the frame is sorted ascending by date (oldest first).
    """
    df = pd.DataFrame(list(rows))
    if df.empty:
        return 0
    df["date"] = pd.to_datetime(df["date"], utc=False)
    df = df.sort_values("date").drop_duplicates(subset=["date"]).reset_index(drop=True)
    atomic_to_parquet(df, ohlcv_path(symbol), engine="pyarrow", index=False)
    return len(df)


def load_ohlcv(symbol: str) -> pd.DataFrame:
    p = ohlcv_path(symbol)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p, engine="pyarrow")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def load_universe_ohlcv(symbols: list[str]) -> pd.DataFrame:
    """Concatenate OHLCV for many symbols into one long-format DataFrame."""
    frames = []
    for sym in symbols:
        df = load_ohlcv(sym)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])


def append_ohlcv_row(symbol: str, row: dict) -> None:
    """Append a single day's OHLCV row (idempotent on date)."""
    existing = load_ohlcv(symbol)
    new = pd.DataFrame([row])
    new["date"] = pd.to_datetime(new["date"])
    if not existing.empty:
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new
    combined = (
        combined.sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )
    atomic_to_parquet(combined, ohlcv_path(symbol), engine="pyarrow", index=False)


def save_snapshot(date_iso: str, payload: dict) -> Path:
    """Persist a full-day connector snapshot to data/snapshots/{date}.json."""
    p = SNAPSHOT_DIR / f"{date_iso}.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def load_snapshot(date_iso: str) -> dict | None:
    p = SNAPSHOT_DIR / f"{date_iso}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_snapshots() -> list[str]:
    return sorted(p.stem for p in SNAPSHOT_DIR.glob("*.json"))
