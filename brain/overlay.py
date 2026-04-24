"""Plan D: LLM defensive overlay.

The overlay does NOT generate buy signals. The mechanical momentum rule in
`brain/strategy.py` does that. The overlay only REDUCES exposure when news or
macro context flags a heightened-risk regime, and triggers emergency EXITS
from held positions on major negative catalysts.

Two endpoints:

  regime_multiplier(macro_context, universe_news) -> ("NORMAL" | "CAUTION"
      | "CRISIS", float multiplier)
    Called on rebalance day. Multiplies the target weight of each pick.

  emergency_exit(symbol, position_news, days_since_entry) -> (bool, reason)
    Called daily for each held position. True means close the position now.

Both endpoints fall back to neutral behavior (NORMAL, no exit) when no LLM
API key is available, so the pipeline still works offline.

Usage of Claude Haiku via the existing `anthropic` client.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent


Regime = Literal["NORMAL", "CAUTION", "CRISIS"]


@dataclass
class RegimeDecision:
    regime: Regime
    multiplier: float
    reason: str
    flags: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str
    severity: float = 0.0
    raw: str = ""


# --------------------------------------------------------------------------
# System prompts
# --------------------------------------------------------------------------
REGIME_SYSTEM = """You are a risk-overlay for a rules-based PSX momentum portfolio.
The portfolio trades via a mechanical monthly rotation. Your SOLE job is to
classify the current macro/market regime into one of three buckets:

  NORMAL   — no unusual macro/political/financial stress. Keep full exposure.
  CAUTION  — elevated stress (rate moves, currency pressure, political noise,
             broad market drawdown not yet at crisis levels). Reduce exposure.
  CRISIS   — acute stress (currency crisis, default risk, coup/violence,
             market-wide circuit breakers, imminent IMF breakdown). Cut exposure
             hard.

Be conservative: default to NORMAL unless there is clear evidence otherwise.
You are NOT trying to time markets — just avoid the catastrophic 1-in-20 months.

Return ONLY valid JSON:
  {"regime": "NORMAL"|"CAUTION"|"CRISIS",
   "confidence": 0.0..1.0,
   "reason": "one sentence",
   "flags": ["...", "..."]}
"""

EXIT_SYSTEM = """You are a risk-overlay for a single open position on the PSX.
Your SOLE job is to decide whether news in the last 3-5 days contains a
material negative catalyst that warrants closing the position before the
monthly rebalance.

Qualifying catalysts (EXIT=true):
  - earnings miss or shock loss
  - regulatory action against the company (fine, suspension, probe)
  - CEO or CFO sudden departure
  - default, debt restructuring, covenant breach
  - strike, plant shutdown, major operational disruption
  - accounting irregularity or fraud accusation
  - merger/acquisition falling through with negative terms

Do NOT exit for:
  - ordinary bearish commentary, analyst downgrades, or "stock is overvalued"
  - general macro/political noise that affects everyone
  - minor operational updates

Be conservative: when in doubt, say EXIT=false.

Return ONLY valid JSON:
  {"exit": true|false,
   "severity": 0.0..1.0,
   "reason": "one sentence"}
"""


# --------------------------------------------------------------------------
# JSON parsing helper
# --------------------------------------------------------------------------
def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1:
        return None
    try:
        return json.loads(s[i: j + 1])
    except json.JSONDecodeError:
        return None


def _anthropic_call(system: str, user: str, model: str = "claude-haiku-4-5",
                    max_tokens: int = 300) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Rule-based fallbacks (when LLM is unavailable)
# --------------------------------------------------------------------------
def _rule_based_regime(macro: dict, universe_5d_change: float | None) -> RegimeDecision:
    """Deterministic fallback when no API key is set."""
    flags = []
    # Crisis triggers
    if universe_5d_change is not None and universe_5d_change < -0.10:
        flags.append(f"Universe down {universe_5d_change:+.1%} in 5d")
    pkr = macro.get("usdpkr")
    pkr_change_5d = macro.get("usdpkr_change_5d")
    if pkr_change_5d is not None and pkr_change_5d > 0.03:
        flags.append(f"PKR weakened {pkr_change_5d:+.1%} in 5d (currency stress)")
    policy_rate = macro.get("policy_rate")
    if policy_rate is not None and policy_rate >= 20:
        flags.append(f"Policy rate elevated at {policy_rate}%")

    if universe_5d_change is not None and universe_5d_change < -0.10:
        return RegimeDecision("CRISIS", 0.50,
                              "Rule-based: universe -10% in 5d", flags)
    if (universe_5d_change is not None and universe_5d_change < -0.05) or \
       (pkr_change_5d is not None and pkr_change_5d > 0.02):
        return RegimeDecision("CAUTION", 0.75,
                              "Rule-based: elevated stress markers", flags)
    return RegimeDecision("NORMAL", 1.00,
                          "Rule-based: no stress markers", flags)


def _rule_based_exit(position_news: list[dict]) -> ExitDecision:
    """Simple keyword-based emergency exit when no LLM is available.

    `position_news` is a list of {title, score} dicts from our sentiment module.
    """
    if not position_news:
        return ExitDecision(False, "No news")
    severe_keywords = (
        "default", "bankruptcy", "fraud", "investigation", "suspension",
        "resign", "scandal", "accused", "probe", "halt",
    )
    for n in position_news:
        title = (n.get("title") or "").lower()
        score = float(n.get("score", 0))
        if score <= -0.6 and any(k in title for k in severe_keywords):
            return ExitDecision(True, f"Keyword match: {title[:80]}",
                                severity=abs(score))
    return ExitDecision(False, "No severe negative catalyst")


# --------------------------------------------------------------------------
# Public endpoints
# --------------------------------------------------------------------------
def regime_multiplier(
    macro_context: dict,
    universe_snapshot: dict,
    market_news: list[dict] | None = None,
    model: str = "claude-haiku-4-5",
) -> RegimeDecision:
    """Classify macro/market regime → exposure multiplier.

    Inputs:
      macro_context : dict with usdpkr, policy_rate, brent, fipi_net, etc.
      universe_snapshot : dict with universe_ret_5d, universe_ret_21d,
                          kse100_change_5d, breadth_pct_up, etc.
      market_news : list of recent market-wide headlines (dicts with title,
                    score, source).
    """
    universe_5d = universe_snapshot.get("universe_ret_5d")

    raw = _anthropic_call(
        REGIME_SYSTEM,
        _format_regime_prompt(macro_context, universe_snapshot, market_news or []),
        model=model,
    )
    if raw is None:
        return _rule_based_regime(macro_context, universe_5d)

    parsed = _parse_json(raw)
    if not parsed or parsed.get("regime") not in ("NORMAL", "CAUTION", "CRISIS"):
        return _rule_based_regime(macro_context, universe_5d)

    regime = parsed["regime"]
    mult = {"NORMAL": 1.00, "CAUTION": 0.75, "CRISIS": 0.50}[regime]
    return RegimeDecision(
        regime=regime,
        multiplier=mult,
        reason=str(parsed.get("reason", ""))[:300],
        flags=list(parsed.get("flags", []))[:5],
        raw=raw[:400],
    )


def emergency_exit(
    symbol: str,
    position_news: list[dict],
    days_since_entry: int,
    model: str = "claude-haiku-4-5",
) -> ExitDecision:
    """Decide if news warrants closing `symbol` mid-month.

    `position_news` is a list of recent headline dicts (title, score, date).
    """
    if not position_news:
        return ExitDecision(False, "No news")

    raw = _anthropic_call(
        EXIT_SYSTEM,
        _format_exit_prompt(symbol, position_news, days_since_entry),
        model=model,
        max_tokens=200,
    )
    if raw is None:
        return _rule_based_exit(position_news)

    parsed = _parse_json(raw)
    if not parsed:
        return _rule_based_exit(position_news)
    return ExitDecision(
        should_exit=bool(parsed.get("exit", False)),
        reason=str(parsed.get("reason", ""))[:300],
        severity=float(parsed.get("severity", 0.0)),
        raw=raw[:300],
    )


# --------------------------------------------------------------------------
# Prompt formatters
# --------------------------------------------------------------------------
def _format_regime_prompt(macro: dict, snap: dict, news: list[dict]) -> str:
    lines = [
        "## Macro context (today)",
        f"- PKR/USD: {macro.get('usdpkr', 'n/a')} "
        f"(5d change: {_pct(macro.get('usdpkr_change_5d'))})",
        f"- SBP policy rate: {macro.get('policy_rate', 'n/a')}%",
        f"- Brent crude: {macro.get('brent', 'n/a')} USD/bbl",
        f"- Gold: {macro.get('gold', 'n/a')} USD/oz",
        f"- FIPI 5d net (PKR mn): {macro.get('fipi_net_5d', 'n/a')}",
        "",
        "## Universe snapshot",
        f"- 15-stock universe mean 5d return: {_pct(snap.get('universe_ret_5d'))}",
        f"- 15-stock universe mean 21d return: {_pct(snap.get('universe_ret_21d'))}",
        f"- KSE-100 5d change: {_pct(snap.get('kse100_change_5d'))}",
        f"- Breadth (% stocks up on day): {snap.get('breadth_pct_up', 'n/a')}",
        "",
        "## Market-wide headlines (last 48h)",
    ]
    if news:
        for i, n in enumerate(news[:10], 1):
            lines.append(f"{i}. [{float(n.get('score', 0)):+.2f}] "
                         f"{(n.get('title') or '')[:120]}")
    else:
        lines.append("(no market-wide headlines available)")

    lines.extend([
        "",
        "## Task",
        "Classify the regime. Return JSON with fields "
        "{regime, confidence, reason, flags}.",
    ])
    return "\n".join(lines)


def _format_exit_prompt(symbol: str, news: list[dict], days: int) -> str:
    lines = [
        f"## Position: {symbol}",
        f"Days since entry: {days}",
        "",
        "## Recent headlines (most recent first)",
    ]
    for i, n in enumerate(news[:8], 1):
        lines.append(f"{i}. [{float(n.get('score', 0)):+.2f}] "
                     f"({n.get('date', '')}) {(n.get('title') or '')[:150]}")
    lines.extend([
        "",
        "## Task",
        "Decide whether to emergency-exit. Return JSON with "
        "{exit, severity, reason}.",
    ])
    return "\n".join(lines)


def _pct(v) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):+.2%}"
    except (TypeError, ValueError):
        return str(v)


# --------------------------------------------------------------------------
# Smoke test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    # Fallback path (no API key)
    d = regime_multiplier(
        macro_context={"usdpkr": 280, "usdpkr_change_5d": 0.04,
                       "policy_rate": 12, "brent": 83, "fipi_net_5d": -300},
        universe_snapshot={"universe_ret_5d": -0.08, "universe_ret_21d": -0.05,
                           "kse100_change_5d": -0.07, "breadth_pct_up": 0.20},
        market_news=[],
    )
    print(f"Regime: {d.regime} (×{d.multiplier:.2f})")
    print(f"  reason: {d.reason}")
    print(f"  flags:  {d.flags}")

    e = emergency_exit("OGDC",
                       position_news=[
                           {"title": "SECP opens probe into OGDC accounting",
                            "score": -0.8, "date": "2026-04-20"},
                       ],
                       days_since_entry=6)
    print(f"\nExit decision: {e.should_exit}")
    print(f"  reason: {e.reason}")
