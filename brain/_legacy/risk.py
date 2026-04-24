"""Rule-based risk manager.

Takes (ml_prob, llm_verdict, sentiment, macro_regime, current_portfolio) and
outputs a discretionary BUY/SELL/HOLD decision with position size.

All rules are explicit and transparent — no black-box sizing. This is
deliberate: the ML + LLM do the "smart" work; the risk manager's job is to
PREVENT big losses, not chase alpha.

Config is co-located so you can edit a single file to adjust all thresholds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"


@dataclass
class RiskConfig:
    # Entry gates ---------------------------------------------------------
    min_ml_prob: float = 0.55
    strong_ml_prob: float = 0.65
    min_training_auc: float = 0.52          # skip stocks with no signal edge
    min_llm_confidence: float = 0.55        # LLM must be at least moderately confident
    allowed_verdicts: tuple[str, ...] = ("STRONG_BUY", "BUY")
    block_on_avoid: bool = True             # LLM AVOID overrides ML
    # Economic gate: require walk-forward avg return per trade to exceed this.
    # Computed by the backtester and cached to models/economic_gate.json.
    # Defaults to 0.8% ~ 2x a 0.40% round-trip cost.
    min_wf_avg_return: float = 0.008
    apply_economic_gate: bool = True

    # Portfolio construction ---------------------------------------------
    max_positions: int = 6
    max_per_sector: int = 2
    base_position_pct: float = 0.12         # 12% of book per BUY
    strong_position_pct: float = 0.18       # 18% for STRONG_BUY

    # Exit rules ---------------------------------------------------------
    trailing_stop_pct: float = 0.08         # -8% from peak
    hard_stop_pct: float = 0.10             # -10% from entry
    take_profit_pct: float = 0.20           # +20% target (optional lock-in)
    max_hold_days: int = 15

    # Regime filter ------------------------------------------------------
    bearish_market_score: float = -0.4      # market sentiment below this -> defensive only
    bearish_defensive_sectors: tuple[str, ...] = ("Banking", "Power", "Fertilizer")
    halt_trading_on_crisis: bool = True     # block all buys when regime=CRISIS


Regime = Literal["NORMAL", "DEFENSIVE", "CRISIS"]


@dataclass
class StockDecision:
    symbol: str
    action: str                       # BUY | HOLD | SELL | SKIP
    size_pct: float                   # fraction of total book
    reason: str
    ml_prob: float
    llm_verdict: str
    llm_confidence: float
    sentiment: float
    training_auc: float | None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def load_training_aucs() -> dict[str, float]:
    """Read models/metrics.json (written by train_models.py)."""
    p = MODELS_DIR / "metrics.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {m["symbol"]: float(m.get("auc", 0)) for m in data}


def load_economic_gate() -> dict[str, dict]:
    """Read models/economic_gate.json (written by brain.backtest).

    Returns {symbol: {avg_return, n_trades, economically_viable, ...}}.
    If the file doesn't exist (backtest never run), returns {} and the
    risk manager skips the economic gate.
    """
    p = MODELS_DIR / "economic_gate.json"
    if not p.exists():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    return {r["symbol"]: r for r in payload.get("rows", [])}


def detect_regime(
    market_sentiment: float,
    kse100_change_5d: float | None,
    fipi_net_5d: float | None,
    cfg: RiskConfig | None = None,
) -> Regime:
    """Classify today's regime from aggregate signals.

    CRISIS: market sentiment very negative AND index -5% over 5d OR FIPI heavy outflow
    DEFENSIVE: market sentiment moderately negative
    NORMAL: everything else
    """
    cfg = cfg or RiskConfig()
    if cfg.halt_trading_on_crisis:
        if market_sentiment < -0.6:
            if (kse100_change_5d is not None and kse100_change_5d < -0.05) or \
               (fipi_net_5d is not None and fipi_net_5d < -500):
                return "CRISIS"
    if market_sentiment < cfg.bearish_market_score:
        return "DEFENSIVE"
    return "NORMAL"


# --------------------------------------------------------------------------
# Main decision function
# --------------------------------------------------------------------------
def decide_per_stock(
    symbol: str,
    sector: str,
    ml_prob: float,
    training_auc: float | None,
    llm_verdict: str,
    llm_confidence: float,
    sentiment_score: float,
    regime: Regime,
    cfg: RiskConfig | None = None,
    economics: dict | None = None,
) -> StockDecision:
    """Decide BUY / HOLD / SKIP for one stock, given all signals + regime.

    `economics` is an optional per-symbol dict from load_economic_gate()
    (i.e. the row for this symbol). When provided and the economic gate is on,
    a stock whose walk-forward avg return/trade is too low is vetoed.
    """
    cfg = cfg or RiskConfig()
    notes: list[str] = []

    def make(action: str, size: float, reason: str) -> StockDecision:
        return StockDecision(
            symbol=symbol, action=action, size_pct=size, reason=reason,
            ml_prob=ml_prob, llm_verdict=llm_verdict,
            llm_confidence=llm_confidence, sentiment=sentiment_score,
            training_auc=training_auc, notes=notes,
        )

    # --- Hard gates ---
    if regime == "CRISIS":
        return make("SKIP", 0.0, "Regime=CRISIS: no new buys")

    if training_auc is not None and training_auc < cfg.min_training_auc:
        return make("SKIP", 0.0,
                    f"Training AUC {training_auc:.2f} below threshold {cfg.min_training_auc}")

    if cfg.apply_economic_gate and economics is not None:
        avg_r = float(economics.get("avg_return", 0.0))
        if avg_r < cfg.min_wf_avg_return:
            return make("SKIP", 0.0,
                        f"Walk-forward avg return/trade {avg_r:+.2%} below "
                        f"threshold {cfg.min_wf_avg_return:+.2%} (dead weight)")

    if ml_prob < cfg.min_ml_prob:
        return make("SKIP", 0.0, f"ML prob {ml_prob:.2f} below threshold {cfg.min_ml_prob}")

    if cfg.block_on_avoid and llm_verdict == "AVOID":
        return make("SKIP", 0.0, "LLM analyst verdict = AVOID (red flag in news/macro)")

    if llm_verdict not in cfg.allowed_verdicts:
        return make("HOLD", 0.0, f"LLM verdict {llm_verdict} — waiting for stronger confirmation")

    if llm_confidence < cfg.min_llm_confidence:
        return make("HOLD", 0.0, f"LLM confidence {llm_confidence:.2f} too low")

    # --- Regime-based sector gating ---
    if regime == "DEFENSIVE" and sector not in cfg.bearish_defensive_sectors:
        notes.append(f"Regime=DEFENSIVE: only {cfg.bearish_defensive_sectors} allowed")
        return make("SKIP", 0.0, "Cyclical blocked in DEFENSIVE regime")

    # --- Sizing ---
    if llm_verdict == "STRONG_BUY" and ml_prob >= cfg.strong_ml_prob:
        size = cfg.strong_position_pct
        reason = f"STRONG_BUY: ML={ml_prob:.2f}, LLM={llm_confidence:.2f}, sent={sentiment_score:+.2f}"
    else:
        size = cfg.base_position_pct
        reason = f"BUY: ML={ml_prob:.2f}, LLM={llm_confidence:.2f}, sent={sentiment_score:+.2f}"

    return make("BUY", size, reason)


def filter_portfolio(
    decisions: list[StockDecision],
    cfg: RiskConfig | None = None,
    open_positions_by_sector: dict[str, int] | None = None,
) -> list[StockDecision]:
    """Apply portfolio-level constraints (max positions, sector caps).

    Keeps the highest-conviction buys when over the cap.
    """
    cfg = cfg or RiskConfig()
    open_positions_by_sector = dict(open_positions_by_sector or {})

    # Rank BUY signals: STRONG_BUY first, then ML*LLM confidence
    def rank_key(d: StockDecision) -> tuple:
        is_strong = 1 if d.llm_verdict == "STRONG_BUY" else 0
        return (is_strong, d.ml_prob * d.llm_confidence)

    buys = sorted([d for d in decisions if d.action == "BUY"],
                  key=rank_key, reverse=True)

    total_buys = sum(open_positions_by_sector.values())
    kept: list[StockDecision] = []
    sector_count = dict(open_positions_by_sector)
    # Load symbol->sector from universe
    from config.universe import sector_of
    for d in buys:
        if total_buys >= cfg.max_positions:
            d.action = "SKIP"
            d.reason = f"Portfolio full ({cfg.max_positions} positions)"
            continue
        sec = sector_of(d.symbol) or "Other"
        if sector_count.get(sec, 0) >= cfg.max_per_sector:
            d.action = "SKIP"
            d.reason = f"Sector cap reached for {sec}"
            continue
        kept.append(d)
        sector_count[sec] = sector_count.get(sec, 0) + 1
        total_buys += 1

    # Non-BUY decisions pass through unchanged
    others = [d for d in decisions if d.action != "BUY" or d not in buys]
    return kept + [d for d in decisions if d.action != "BUY"]


# --------------------------------------------------------------------------
# Exit rules for open positions
# --------------------------------------------------------------------------
@dataclass
class ExitDecision:
    symbol: str
    should_exit: bool
    reason: str


def check_exit(
    symbol: str,
    entry_px: float,
    peak_px: float,
    current_px: float,
    hold_days: int,
    ml_prob: float,
    llm_verdict: str,
    cfg: RiskConfig | None = None,
) -> ExitDecision:
    """Decide whether to close an existing position."""
    cfg = cfg or RiskConfig()

    # Hard stop from entry
    if current_px <= entry_px * (1 - cfg.hard_stop_pct):
        return ExitDecision(symbol, True,
                            f"Hard stop hit: px {current_px:.2f} vs entry {entry_px:.2f}")

    # Trailing stop from peak (only after position is profitable)
    if peak_px > entry_px and current_px <= peak_px * (1 - cfg.trailing_stop_pct):
        return ExitDecision(symbol, True,
                            f"Trailing stop: px {current_px:.2f} vs peak {peak_px:.2f}")

    # Take-profit
    if current_px >= entry_px * (1 + cfg.take_profit_pct):
        return ExitDecision(symbol, True,
                            f"Take-profit: +{(current_px/entry_px - 1):.1%} from entry")

    # Max hold
    if hold_days >= cfg.max_hold_days:
        return ExitDecision(symbol, True, f"Max hold {cfg.max_hold_days}d reached")

    # LLM flipped to AVOID
    if llm_verdict == "AVOID":
        return ExitDecision(symbol, True, "LLM flipped to AVOID")

    # Signal decayed
    if ml_prob < 0.45:
        return ExitDecision(symbol, True, f"ML signal decayed to {ml_prob:.2f}")

    return ExitDecision(symbol, False, "Hold")
