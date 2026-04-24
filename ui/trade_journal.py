"""Trade journal — closed-position history with realized P&L.

Every time the user closes a position in the UI, the full round-trip is
archived here. This is the system of record for realized performance and
drives:
  - win rate, avg winner, avg loser
  - total realized P&L (gross and NET of PSX transaction costs)
  - trade-by-trade comparison against the advisor's recommendation at entry

Storage: `data/trade_journal.json`

Entry schema (version 1):
    {
      "symbol": "MCB",
      "quantity": 100,
      "entry_date": "2026-03-01",
      "entry_price": 380.0,
      "exit_date":  "2026-04-15",
      "exit_price": 412.5,
      "exit_reason": "target",          # "target" | "stop" | "signal_decay" |
                                        #   "manual" | "time_exit" | other
      "entry_notes": "bought after earnings beat",
      "exit_notes":  "trailing stop hit on gap down",
      "hold_days": 45,
      "gross_return_pct": 8.55,
      "net_return_pct":   6.10,          # after PSX costs + slippage + CGT
      "gross_pnl_pkr":    3250.00,
      "net_pnl_pkr":      2320.00
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.costs import net_return_pct, round_trip_cost_pct

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = PROJECT_ROOT / "data" / "trade_journal.json"


def _default() -> dict:
    return {"version": 1, "trades": []}


def _read() -> dict:
    if not JOURNAL_PATH.exists():
        return _default()
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or "trades" not in d:
            return _default()
        return d
    except (OSError, json.JSONDecodeError):
        return _default()


def _write(data: dict) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_journal() -> list[dict]:
    return list(_read().get("trades", []))


def _hold_days(entry_date: str, exit_date: str) -> Optional[int]:
    try:
        a = datetime.strptime(entry_date, "%Y-%m-%d")
        b = datetime.strptime(exit_date, "%Y-%m-%d")
        return max(0, (b - a).days)
    except Exception:
        return None


def append_trade(*, symbol: str, quantity: float,
                 entry_date: str, entry_price: float,
                 exit_date: str, exit_price: float,
                 exit_reason: str = "manual",
                 entry_notes: str = "", exit_notes: str = "") -> dict:
    """Append a fully-closed trade to the journal. Returns the stored entry."""
    qty = float(quantity)
    ent = float(entry_price)
    exi = float(exit_price)
    gross_pct = (exi / ent - 1) * 100.0
    net_pct = net_return_pct(gross_pct, apply_cgt=(gross_pct > 0))
    gross_pnl = (exi - ent) * qty
    # Net PKR approximates gross PKR scaled by (net_pct / gross_pct) so users
    # can see cost-adjusted P&L in the same currency.
    if gross_pct != 0:
        net_pnl = gross_pnl * (net_pct / gross_pct)
    else:
        # No gross move but costs still applied
        net_pnl = -abs(round_trip_cost_pct() / 100.0) * ent * qty

    entry = {
        "symbol": symbol.strip().upper(),
        "quantity": qty,
        "entry_date": str(entry_date),
        "entry_price": round(ent, 2),
        "exit_date": str(exit_date),
        "exit_price": round(exi, 2),
        "exit_reason": exit_reason or "manual",
        "entry_notes": str(entry_notes or "")[:500],
        "exit_notes": str(exit_notes or "")[:500],
        "hold_days": _hold_days(str(entry_date), str(exit_date)),
        "gross_return_pct": round(gross_pct, 2),
        "net_return_pct": round(net_pct, 2),
        "gross_pnl_pkr": round(gross_pnl, 2),
        "net_pnl_pkr": round(net_pnl, 2),
    }

    data = _read()
    data["trades"].append(entry)
    _write(data)
    return entry


def remove_trade(index: int) -> None:
    data = _read()
    if 0 <= index < len(data["trades"]):
        data["trades"].pop(index)
        _write(data)


def journal_stats() -> dict:
    trades = load_journal()
    n = len(trades)
    if n == 0:
        return {"count": 0, "win_rate_pct": 0.0, "total_gross_pnl_pkr": 0.0,
                "total_net_pnl_pkr": 0.0, "avg_winner_pct": 0.0,
                "avg_loser_pct": 0.0, "best_pct": 0.0, "worst_pct": 0.0,
                "avg_hold_days": 0.0}
    winners = [t for t in trades if t["net_return_pct"] > 0]
    losers = [t for t in trades if t["net_return_pct"] <= 0]
    hold_days = [t["hold_days"] for t in trades if t.get("hold_days") is not None]

    return {
        "count": n,
        "win_rate_pct": round(100.0 * len(winners) / n, 1),
        "total_gross_pnl_pkr": round(sum(t["gross_pnl_pkr"] for t in trades), 2),
        "total_net_pnl_pkr":   round(sum(t["net_pnl_pkr"]   for t in trades), 2),
        "avg_winner_pct": round(
            sum(t["net_return_pct"] for t in winners) / max(1, len(winners)), 2),
        "avg_loser_pct": round(
            sum(t["net_return_pct"] for t in losers) / max(1, len(losers)), 2),
        "best_pct":  round(max(t["net_return_pct"] for t in trades), 2),
        "worst_pct": round(min(t["net_return_pct"] for t in trades), 2),
        "avg_hold_days": round(sum(hold_days) / max(1, len(hold_days)), 1),
    }


if __name__ == "__main__":
    from rich import print
    print(journal_stats())
    print(load_journal()[-3:])
