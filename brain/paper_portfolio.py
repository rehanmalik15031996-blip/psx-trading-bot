"""Persistent paper-trading portfolio.

A JSON file at data/paper_portfolio.json holds:
  - cash: PKR available
  - open_positions: dict of symbol -> {entry_date, entry_px, shares, peak_px,
                                        hold_days, entry_prob, entry_reason}
  - closed_trades: list of completed trade records
  - equity_history: list of {date, equity, cash, positions_value}

All methods mutate the file atomically (write-tmp-rename). Thread-safety is
not a concern since our daily pipeline runs sequentially.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "data" / "paper_portfolio.json"

INITIAL_CAPITAL = 1_000_000.0     # PKR 10 lakh


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_px: float
    shares: int
    peak_px: float
    hold_days: int = 0
    entry_prob: float = 0.0
    entry_reason: str = ""

    @property
    def cost(self) -> float:
        return self.shares * self.entry_px

    def mtm(self, cur_px: float) -> float:
        return self.shares * cur_px

    def pnl(self, cur_px: float) -> float:
        return (cur_px - self.entry_px) * self.shares

    def return_pct(self, cur_px: float) -> float:
        return cur_px / self.entry_px - 1 if self.entry_px else 0


@dataclass
class ClosedTrade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_px: float
    exit_px: float
    shares: int
    pnl_pkr: float
    return_pct: float
    hold_days: int
    exit_reason: str
    entry_reason: str = ""


@dataclass
class PortfolioState:
    cash: float = INITIAL_CAPITAL
    open_positions: dict[str, Position] = field(default_factory=dict)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    equity_history: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    last_update: str = field(default_factory=_now_iso)


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def load() -> PortfolioState:
    if not STATE_PATH.exists():
        state = PortfolioState()
        save(state)
        return state
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state = PortfolioState(
        cash=float(raw.get("cash", INITIAL_CAPITAL)),
        open_positions={
            s: Position(**p) for s, p in raw.get("open_positions", {}).items()
        },
        closed_trades=[ClosedTrade(**t) for t in raw.get("closed_trades", [])],
        equity_history=list(raw.get("equity_history", [])),
        created_at=raw.get("created_at", _now_iso()),
        last_update=raw.get("last_update", _now_iso()),
    )
    return state


def save(state: PortfolioState) -> None:
    state.last_update = _now_iso()
    payload = {
        "cash": state.cash,
        "open_positions": {s: asdict(p) for s, p in state.open_positions.items()},
        "closed_trades": [asdict(t) for t in state.closed_trades],
        "equity_history": state.equity_history,
        "created_at": state.created_at,
        "last_update": state.last_update,
    }
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def reset(capital: float = INITIAL_CAPITAL) -> PortfolioState:
    """Wipe the portfolio and start fresh."""
    state = PortfolioState(cash=capital)
    save(state)
    return state


# --------------------------------------------------------------------------
# Trading operations
# --------------------------------------------------------------------------
def open_position(
    state: PortfolioState,
    symbol: str,
    target_pct: float,
    price: float,
    entry_prob: float,
    reason: str,
    commission_bps: float = 10,
    slippage_bps: float = 10,
) -> Position | None:
    """Open a new position sized at `target_pct` of TOTAL EQUITY (not cash).

    Total equity = cash + marked value of open positions at `price`.
    Returns the Position or None if sizing is unfeasible.
    """
    if symbol in state.open_positions:
        return None
    total_equity = state.cash + sum(
        p.shares * price for p in state.open_positions.values()
    )
    alloc = total_equity * target_pct
    if alloc > state.cash:
        alloc = state.cash * 0.98  # leave buffer
    if alloc < 10_000:
        return None

    fill_px = price * (1 + slippage_bps / 10_000)
    shares = int(alloc // fill_px)
    if shares <= 0:
        return None
    gross = shares * fill_px
    fee = gross * (commission_bps / 10_000)
    if state.cash < gross + fee:
        return None

    state.cash -= (gross + fee)
    p = Position(
        symbol=symbol,
        entry_date=date.today().isoformat(),
        entry_px=fill_px,
        shares=shares,
        peak_px=fill_px,
        hold_days=0,
        entry_prob=entry_prob,
        entry_reason=reason,
    )
    state.open_positions[symbol] = p
    return p


def close_position(
    state: PortfolioState,
    symbol: str,
    price: float,
    reason: str,
    commission_bps: float = 10,
    slippage_bps: float = 10,
) -> ClosedTrade | None:
    if symbol not in state.open_positions:
        return None
    pos = state.open_positions[symbol]
    fill_px = price * (1 - slippage_bps / 10_000)
    gross = pos.shares * fill_px
    fee = gross * (commission_bps / 10_000)
    state.cash += (gross - fee)

    pnl = (fill_px - pos.entry_px) * pos.shares - fee
    trade = ClosedTrade(
        symbol=symbol,
        entry_date=pos.entry_date,
        exit_date=date.today().isoformat(),
        entry_px=pos.entry_px,
        exit_px=fill_px,
        shares=pos.shares,
        pnl_pkr=round(pnl, 2),
        return_pct=round(pnl / (pos.shares * pos.entry_px), 4),
        hold_days=pos.hold_days,
        exit_reason=reason,
        entry_reason=pos.entry_reason,
    )
    state.closed_trades.append(trade)
    del state.open_positions[symbol]
    return trade


def mark_to_market(state: PortfolioState, prices: dict[str, float]) -> dict:
    """Update peak_px / hold_days and snapshot equity."""
    positions_value = 0.0
    for sym, pos in state.open_positions.items():
        p = prices.get(sym)
        if p is None:
            positions_value += pos.shares * pos.entry_px
            continue
        pos.peak_px = max(pos.peak_px, p)
        pos.hold_days += 1
        positions_value += pos.shares * p
    equity = state.cash + positions_value
    snap = {
        "date": date.today().isoformat(),
        "equity": round(equity, 2),
        "cash": round(state.cash, 2),
        "positions_value": round(positions_value, 2),
        "n_positions": len(state.open_positions),
    }
    state.equity_history.append(snap)
    return snap


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
def summary(state: PortfolioState, prices: dict[str, float] | None = None) -> dict:
    prices = prices or {}
    total_pnl_closed = sum(t.pnl_pkr for t in state.closed_trades)
    open_value = sum(
        pos.shares * prices.get(sym, pos.entry_px)
        for sym, pos in state.open_positions.items()
    )
    equity = state.cash + open_value
    wins = [t for t in state.closed_trades if t.pnl_pkr > 0]
    losses = [t for t in state.closed_trades if t.pnl_pkr <= 0]
    win_rate = len(wins) / len(state.closed_trades) if state.closed_trades else 0
    return {
        "cash": round(state.cash, 2),
        "open_positions": len(state.open_positions),
        "open_positions_value": round(open_value, 2),
        "total_equity": round(equity, 2),
        "total_pnl_closed": round(total_pnl_closed, 2),
        "total_return_pct": round(equity / INITIAL_CAPITAL - 1, 4),
        "n_closed_trades": len(state.closed_trades),
        "win_rate": round(win_rate, 3),
        "last_update": state.last_update,
    }
