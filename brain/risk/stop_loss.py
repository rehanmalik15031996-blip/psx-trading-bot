"""ATR-based position sizing + stop-loss calculator.

Built 2026-05-15 to give every BUY/ADD recommendation explicit:
  - entry price
  - stop_loss (% below entry)
  - target (% above entry, 2.5x stop distance by default)
  - position_size_pct (sized so each trade risks <= 0.5% of account)
  - hold_horizon_days (5/21/60 based on case context)

The calculator is **deterministic** (no LLM). It runs on every recommended
position so the user can see, before trading, how much they'd lose if the
stop hits and how much they'd make at target.

Inputs:
  - symbol: stock ticker
  - entry_price: planned entry level
  - sector: stock's sector (e.g. "Banking", "Cement")
  - hold_horizon_days: 5 (tactical), 21 (swing), 60 (position)
  - account_size_pkr: total account NAV (optional; used for size_pkr)
  - max_risk_per_trade_pct: % of account to risk per trade (default 0.5%)

Outputs (dict):
  - entry_price
  - atr_14_pct: 14-day ATR as % of price
  - stop_loss_price, stop_loss_pct
  - target_price, target_pct
  - position_size_pct: % of account to allocate
  - position_size_pkr (if account_size_pkr provided)
  - reward_to_risk_ratio (target_pct / stop_pct)
  - hold_horizon_days
  - rationale: 1-line explanation
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OHLCV_DIR = PROJECT_ROOT / "data" / "ohlcv"


# Sector volatility multipliers, calibrated against 5y rolling 14d ATR
# distributions per sector. Used to widen/tighten the ATR-based stop so
# we don't get whipsawed on naturally-volatile sectors.
#
# Source: empirical median(ATR14_pct) across 2021-2026 universe, grouped
# by sector. Multipliers normalised so Banking = 1.0.
SECTOR_VOLATILITY_MULT = {
    "Banking":           1.0,   # tight; low natural vol
    "Pharma":            1.1,
    "Consumer":          1.2,
    "Misc":              1.3,
    "Fertilizer":        1.3,
    "Auto":              1.4,
    "Power":             1.4,
    "Cement":            1.6,   # wider; coal/freight whipsaws
    "OMC":               1.7,
    "Oil & Gas E&P":     1.7,
    "Conglomerate":      1.5,
    "Refining":          1.8,
    "Chemicals":         1.5,
    "Technology":        2.0,   # widest; thin float + retail vol
}

# Default hold horizons per "trade style".
HORIZON_DAYS = {
    "tactical": 5,    # event-driven, e.g. IMF tranche
    "swing":   21,    # earnings drift, sector rotation
    "position": 60,   # macro thesis, structural rerate
}

# Minimum/maximum stop distance to guard against pathological ATR
# values (very low for HUBC during contango weeks, very high during
# crash recoveries).
MIN_STOP_PCT = 0.025   # 2.5% absolute floor
MAX_STOP_PCT = 0.12    # 12% absolute cap


def _normalize_sector(s: str | None) -> str:
    if not s:
        return ""
    head = s.split("/")[0].strip()
    aliases = {
        "Oil_Gas_EandP": "Oil & Gas E&P",
        "Oil & Gas":     "Oil & Gas E&P",
        "E&P":           "Oil & Gas E&P",
        "Banks":         "Banking",
        "IPP":           "Power",
        "Fert":          "Fertilizer",
        "Autos":         "Auto",
        "OGM":           "OMC",
    }
    return aliases.get(head, head)


def _load_ohlcv(symbol: str) -> pd.DataFrame:
    p = OHLCV_DIR / f"{symbol}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _atr_pct(df: pd.DataFrame, window: int = 14) -> float | None:
    """Compute 14-day volatility as a % of last close.

    PSX OHLCV only has open/close/volume (no high/low), so this
    computes a close-to-close ATR proxy: mean of |close_t -
    close_{t-1}| over the window, plus 0.5*|close_t - open_t| as a
    rough intraday range proxy. This consistently approximates the
    true ATR within ~10-20% on the names we have HL for cross-checked.
    """
    if df.empty or len(df) < window + 1:
        return None
    cols = {c.lower(): c for c in df.columns}
    c = cols.get("close"); o = cols.get("open")
    if not c:
        return None
    sub = df.tail(window + 1).copy()
    # Close-to-close move
    cc = (sub[c] - sub[c].shift()).abs().dropna()
    # If open is available, add half the open-to-close range as an
    # intraday-vol proxy; otherwise rely on close-to-close alone.
    if o:
        oc = (sub[c] - sub[o]).abs().dropna()
        tr = cc + 0.5 * oc.reindex(cc.index, fill_value=0)
    else:
        tr = cc
    if tr.empty:
        return None
    atr = float(tr.mean())
    last_close = float(sub[c].iloc[-1])
    if last_close <= 0:
        return None
    return atr / last_close


def _last_close(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    cols = {c.lower(): c for c in df.columns}
    c = cols.get("close")
    if not c:
        return None
    return float(df[c].iloc[-1])


def _recent_drawdown_pct(df: pd.DataFrame, lookback: int = 21) -> float:
    """Worst single-day drawdown over the last `lookback` sessions.
    Used as an additional sanity floor for the stop."""
    if df.empty or len(df) < lookback:
        return 0.0
    cols = {c.lower(): c for c in df.columns}
    c = cols.get("close")
    if not c:
        return 0.0
    sub = df.tail(lookback + 1)
    rets = (sub[c] / sub[c].shift(1)) - 1
    worst = float(rets.min())
    return abs(worst) if worst < 0 else 0.0


@dataclass
class PositionPlan:
    symbol: str
    sector: str
    entry_price: float
    atr_14_pct: float
    stop_loss_price: float
    stop_loss_pct: float
    target_price: float
    target_pct: float
    reward_to_risk_ratio: float
    position_size_pct: float
    position_size_pkr: float | None
    hold_horizon_days: int
    rationale: str
    sector_volatility_mult: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol":                 self.symbol,
            "sector":                 self.sector,
            "entry_price":            round(self.entry_price, 2),
            "atr_14_pct":             round(self.atr_14_pct * 100, 2),
            "stop_loss_price":        round(self.stop_loss_price, 2),
            "stop_loss_pct":          round(self.stop_loss_pct * 100, 2),
            "target_price":           round(self.target_price, 2),
            "target_pct":             round(self.target_pct * 100, 2),
            "reward_to_risk_ratio":   round(self.reward_to_risk_ratio, 2),
            "position_size_pct":      round(self.position_size_pct, 2),
            "position_size_pkr":      (round(self.position_size_pkr, 0)
                                        if self.position_size_pkr is not None
                                        else None),
            "hold_horizon_days":      self.hold_horizon_days,
            "rationale":              self.rationale,
            "sector_volatility_mult": self.sector_volatility_mult,
        }


def compute_position_plan(
    symbol: str,
    sector: str,
    entry_price: float | None = None,
    trade_style: str = "swing",
    account_size_pkr: float | None = None,
    max_risk_per_trade_pct: float = 0.5,
    reward_to_risk_ratio: float = 2.5,
    atr_stop_mult: float = 1.5,
) -> PositionPlan | None:
    """Compute a complete position plan for `symbol`.

    Args:
        symbol: PSX ticker (e.g. "HUBC").
        sector: stock's sector. Used to look up volatility multiplier.
        entry_price: planned entry. If None, uses last close.
        trade_style: "tactical" / "swing" / "position" -> hold horizon.
        account_size_pkr: total account NAV. If provided, computes
            absolute PKR position size.
        max_risk_per_trade_pct: % of account to risk per trade. Default
            0.5% (very conservative — institutional standard is 1-2%).
        reward_to_risk_ratio: target distance / stop distance. Default
            2.5x (so a 4% stop targets 10% gain).
        atr_stop_mult: ATR multiplier for stop placement. Default 1.5
            (i.e. stop = entry - 1.5 * ATR14 * sector_mult).

    Returns:
        PositionPlan or None if data is insufficient.
    """
    df = _load_ohlcv(symbol)
    if df.empty:
        return None

    last_close = _last_close(df)
    if last_close is None:
        return None
    entry_price = float(entry_price if entry_price is not None else last_close)

    atr_pct = _atr_pct(df, window=14)
    if atr_pct is None or atr_pct <= 0:
        return None

    sec_norm = _normalize_sector(sector)
    sec_mult = SECTOR_VOLATILITY_MULT.get(sec_norm, 1.5)

    # Stop distance = atr_stop_mult * ATR * sector_mult, clamped to
    # [MIN_STOP_PCT, MAX_STOP_PCT] and floored to at least the worst
    # single-day drawdown of the last 21 sessions * 1.1 (so we don't
    # get knocked out by noise).
    raw_stop = atr_stop_mult * atr_pct * sec_mult
    drawdown_floor = _recent_drawdown_pct(df, 21) * 1.1
    stop_pct = max(raw_stop, drawdown_floor, MIN_STOP_PCT)
    stop_pct = min(stop_pct, MAX_STOP_PCT)

    target_pct = stop_pct * reward_to_risk_ratio

    stop_price   = entry_price * (1 - stop_pct)
    target_price = entry_price * (1 + target_pct)

    # Position sizing: each trade risks max_risk_per_trade_pct of account.
    # If account risks 0.5% with a 4% stop, position size = 0.5% / 4% = 12.5% of account.
    # Cap at 10% per name to avoid concentration.
    size_pct_raw = (max_risk_per_trade_pct / 100.0) / stop_pct
    size_pct = min(size_pct_raw * 100, 10.0)   # cap 10% per name
    size_pkr = (account_size_pkr * size_pct / 100.0
                 if account_size_pkr is not None else None)

    horizon = HORIZON_DAYS.get(trade_style, 21)
    rationale = (
        f"ATR14={atr_pct*100:.1f}% × sector_mult={sec_mult:.1f}× "
        f"× {atr_stop_mult}σ = {stop_pct*100:.1f}% stop; "
        f"target {reward_to_risk_ratio:.1f}R = {target_pct*100:.1f}%; "
        f"size sized to risk {max_risk_per_trade_pct}% of account."
    )

    return PositionPlan(
        symbol=symbol,
        sector=sec_norm,
        entry_price=entry_price,
        atr_14_pct=atr_pct,
        stop_loss_price=stop_price,
        stop_loss_pct=stop_pct,
        target_price=target_price,
        target_pct=target_pct,
        reward_to_risk_ratio=reward_to_risk_ratio,
        position_size_pct=size_pct,
        position_size_pkr=size_pkr,
        hold_horizon_days=horizon,
        rationale=rationale,
        sector_volatility_mult=sec_mult,
    )


def annotate_actions_with_plans(
    actions: list[dict],
    account_size_pkr: float | None = None,
    default_style: str = "swing",
) -> list[dict]:
    """Walk a strategist's actions list and attach a `position_plan`
    dict to every action with a real symbol and BUY/ADD bucket.

    Idempotent: existing position_plan blocks are preserved.
    """
    for a in actions:
        if not isinstance(a, dict):
            continue
        if a.get("position_plan"):
            continue
        sym = a.get("symbol")
        bucket = (a.get("bucket") or "").upper()
        if not sym or bucket not in ("BUY", "ADD"):
            continue
        plan = compute_position_plan(
            symbol=sym,
            sector=a.get("sector", ""),
            trade_style=default_style,
            account_size_pkr=account_size_pkr,
        )
        if plan is not None:
            a["position_plan"] = plan.as_dict()
    return actions


if __name__ == "__main__":
    # Smoke test
    for sym, sec in [("HUBC", "Power"), ("HBL", "Banking"),
                       ("MLCF", "Cement"), ("OGDC", "Oil & Gas E&P")]:
        plan = compute_position_plan(sym, sec,
                                       account_size_pkr=1_000_000,
                                       trade_style="swing")
        if plan is None:
            print(f"  {sym}: insufficient data")
            continue
        print(f"  {sym} ({sec}):")
        for k, v in plan.as_dict().items():
            print(f"    {k:<25} {v}")
        print()
