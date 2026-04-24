"""User portfolio persistence (separate from paper_portfolio).

The bot's own paper portfolio lives in `data/paper_portfolio.json`. THIS file
is for the user's REAL positions entered via the UI. We never touch it from the
strategy code — it's advisory context only. The LLM reads it via the
`get_user_portfolio` tool but can never modify it; the user edits through
Streamlit widgets.

Schema of `data/user_portfolio.json`:

    {
      "version": 1,
      "positions": [
        {
          "symbol": "MCB",
          "entry_date": "2026-03-15",
          "entry_price": 380.0,
          "quantity": 100,
          "notes": "bought after earnings beat"
        },
        ...
      ]
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = PROJECT_ROOT / "data" / "user_portfolio.json"


def _default() -> dict:
    return {"version": 1, "positions": []}


def _read() -> dict:
    if not PORTFOLIO_PATH.exists():
        return _default()
    try:
        with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or "positions" not in d:
            return _default()
        return d
    except (OSError, json.JSONDecodeError):
        return _default()


def _write(data: dict) -> None:
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_user_portfolio() -> list[dict]:
    return list(_read().get("positions", []))


def save_user_portfolio(positions: list[dict]) -> None:
    """Replace the full portfolio with the given list of positions."""
    cleaned = []
    for p in positions:
        sym = str(p.get("symbol", "")).strip().upper()
        if not sym:
            continue
        try:
            ent_px = float(p.get("entry_price", 0))
            qty = float(p.get("quantity", 0))
        except (TypeError, ValueError):
            continue
        if ent_px <= 0 or qty <= 0:
            continue
        cleaned.append({
            "symbol": sym,
            "entry_date": str(p.get("entry_date") or
                              datetime.now().strftime("%Y-%m-%d")),
            "entry_price": ent_px,
            "quantity": qty,
            "notes": str(p.get("notes", "") or "")[:200],
        })
    _write({"version": 1, "positions": cleaned})


def add_position(symbol: str, entry_price: float, quantity: float,
                 entry_date: str | None = None, notes: str = "") -> None:
    positions = load_user_portfolio()
    positions.append({
        "symbol": symbol.strip().upper(),
        "entry_date": entry_date or datetime.now().strftime("%Y-%m-%d"),
        "entry_price": float(entry_price),
        "quantity": float(quantity),
        "notes": notes,
    })
    save_user_portfolio(positions)


def remove_position(index: int) -> None:
    positions = load_user_portfolio()
    if 0 <= index < len(positions):
        positions.pop(index)
        save_user_portfolio(positions)


def close_position(index: int, *, exit_price: float, exit_date: str | None = None,
                   exit_reason: str = "manual", exit_notes: str = "") -> dict:
    """Close a live position: record the full round-trip in the trade journal
    and remove the position from the active portfolio.

    Returns the stored journal entry (includes gross/net P&L in PKR and %),
    or a dict with an "error" key if the index is out of range.
    """
    from ui.trade_journal import append_trade

    positions = load_user_portfolio()
    if not (0 <= index < len(positions)):
        return {"error": f"position index {index} out of range "
                         f"(have {len(positions)})"}
    p = positions[index]
    entry = append_trade(
        symbol=p["symbol"],
        quantity=float(p.get("quantity", 0)),
        entry_date=str(p.get("entry_date") or ""),
        entry_price=float(p["entry_price"]),
        exit_date=str(exit_date or datetime.now().strftime("%Y-%m-%d")),
        exit_price=float(exit_price),
        exit_reason=exit_reason,
        entry_notes=str(p.get("notes", "") or ""),
        exit_notes=exit_notes,
    )
    positions.pop(index)
    save_user_portfolio(positions)
    return entry


if __name__ == "__main__":
    from rich import print
    print(load_user_portfolio())
