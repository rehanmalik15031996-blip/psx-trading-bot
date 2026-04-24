"""User watchlist persistence.

Separate from the portfolio (`ui/portfolio.py`) and the bot's trading universe
(`config/universe.py`). The watchlist is a light-weight "keep an eye on this"
list: a symbol plus an optional target price, stop-alert level, and free-form
note. The UI renders it with live quotes and momentum rank; the LLM reads it
via `tools.get_watchlist()` so the chatbot knows which names you care about
beyond the ones you currently own.

Storage:  `data/user_watchlist.json`

Schema (version 1):
    {
      "version": 1,
      "items": [
        {
          "symbol": "LUCK",
          "added_date": "2026-04-24",
          "target_price": 820.0,          # optional
          "alert_above": 820.0,           # optional
          "alert_below": 650.0,           # optional
          "note": "break above 820 with volume"
        },
        ...
      ]
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = PROJECT_ROOT / "data" / "user_watchlist.json"


def _default() -> dict:
    return {"version": 1, "items": []}


def _read() -> dict:
    if not WATCHLIST_PATH.exists():
        return _default()
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or "items" not in d:
            return _default()
        return d
    except (OSError, json.JSONDecodeError):
        return _default()


def _write(data: dict) -> None:
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_watchlist() -> list[dict]:
    return list(_read().get("items", []))


def save_watchlist(items: list[dict]) -> None:
    cleaned: list[dict] = []
    seen: set[str] = set()
    for it in items:
        sym = str(it.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        cleaned.append({
            "symbol": sym,
            "added_date": str(it.get("added_date")
                              or datetime.now().strftime("%Y-%m-%d")),
            "target_price": _to_float_or_none(it.get("target_price")),
            "alert_above": _to_float_or_none(it.get("alert_above")),
            "alert_below": _to_float_or_none(it.get("alert_below")),
            "note": str(it.get("note", "") or "")[:200],
        })
    _write({"version": 1, "items": cleaned})


def _to_float_or_none(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def add_to_watchlist(symbol: str, *, target_price: Optional[float] = None,
                     alert_above: Optional[float] = None,
                     alert_below: Optional[float] = None,
                     note: str = "") -> None:
    """Add a symbol to the watchlist. If it already exists, update it in place."""
    items = load_watchlist()
    sym = symbol.strip().upper()
    for it in items:
        if it["symbol"] == sym:
            it["target_price"] = _to_float_or_none(target_price) \
                or it.get("target_price")
            it["alert_above"] = _to_float_or_none(alert_above) \
                or it.get("alert_above")
            it["alert_below"] = _to_float_or_none(alert_below) \
                or it.get("alert_below")
            if note:
                it["note"] = note[:200]
            save_watchlist(items)
            return
    items.append({
        "symbol": sym,
        "added_date": datetime.now().strftime("%Y-%m-%d"),
        "target_price": _to_float_or_none(target_price),
        "alert_above": _to_float_or_none(alert_above),
        "alert_below": _to_float_or_none(alert_below),
        "note": note,
    })
    save_watchlist(items)


def remove_from_watchlist(symbol: str) -> None:
    sym = symbol.strip().upper()
    items = [it for it in load_watchlist() if it["symbol"] != sym]
    save_watchlist(items)


if __name__ == "__main__":
    from rich import print
    print(load_watchlist())
