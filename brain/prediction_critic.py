"""Critic self-review pass for daily predictions.

Why this module exists
----------------------
The LLM strategist that produces the daily prediction is good at
synthesis but occasionally publishes an internally inconsistent call:

    direction = "BULLISH",  conviction = "HIGH"
    key_drivers   = ["RSI 28 oversold", "5d return -8%"]
    key_risks     = ["all macro indicators bearish", ...]

That is not a defensible call to send to an analyst. The deterministic
:class:`PredictionCritic` runs a small set of hand-crafted checks
*after* the LLM has produced its JSON but *before* the prediction is
written to disk. If a check trips, the critic either downgrades the
conviction or rewrites the action, and stamps a ``critic_notes``
field so the analyst can see exactly what was caught.

The checks are intentionally narrow (high precision, low recall): we
do not want to second-guess the LLM on every nuance — only catch the
gross logic errors that erode trust. Each check has a brief docstring
explaining what real-world failure mode it protects against.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KSE100_PATH = PROJECT_ROOT / "data" / "macro" / "kse100.parquet"


@dataclass
class CriticVerdict:
    """One critic check result.

    Attributes
    ----------
    severity : str
        ``info`` → cosmetic only, no action.
        ``warn`` → conviction is downgraded one notch.
        ``fail`` → action is forced to ``HOLD``, conviction → LOW.
    note : str
        Plain-English description added to ``critic_notes``.
    """
    severity: str
    note: str


def _direction_from_action(action: str) -> str:
    if action in ("BUY", "ADD"):  return "BULLISH"
    if action in ("AVOID", "SELL", "TRIM"): return "BEARISH"
    return "NEUTRAL"


def _check_direction_action_consistency(pred: dict) -> CriticVerdict | None:
    """Catch the case where the LLM says BULLISH but suggests AVOID
    (or any sign mismatch). Real example: a BULLISH call with
    ``suggested_action = "AVOID"`` and a stop above the entry price.
    """
    d = (pred.get("direction") or "").upper()
    a = (pred.get("suggested_action") or "").upper()
    if not d or not a:
        return None
    expected = _direction_from_action(a)
    if expected == "NEUTRAL" or d == expected:
        return None
    return CriticVerdict(
        severity="fail",
        note=(f"direction={d} contradicts suggested_action={a} "
              f"({expected} would be expected) — forced HOLD."),
    )


def _check_drivers_match_direction(pred: dict) -> CriticVerdict | None:
    """A BULLISH call with bearish key drivers (e.g. 'RSI oversold',
    '5d return -8%') is almost always a sign the LLM read the data
    upside-down. Downgrade conviction.
    """
    d = (pred.get("direction") or "").upper()
    drivers = pred.get("key_drivers") or []
    if not drivers or d == "NEUTRAL":
        return None

    bear_words = ("oversold", "decline", "down ", "fall", "drop",
                  "headwind", "negative", "weak", "bearish", "loss",
                  "missed", "below", "stress")
    bull_words = ("uptrend", "rally", "above", "tailwind", "positive",
                  "strong", "bullish", "beat", "gain", "rise",
                  "support", "breakout")

    bear_hits = sum(any(b in str(s).lower() for b in bear_words)
                     for s in drivers)
    bull_hits = sum(any(b in str(s).lower() for b in bull_words)
                     for s in drivers)

    if d == "BULLISH" and bear_hits >= 2 and bull_hits == 0:
        return CriticVerdict(
            severity="warn",
            note=(f"BULLISH call but {bear_hits} of "
                  f"{len(drivers)} key drivers read bearish — "
                  f"downgraded conviction one notch."),
        )
    if d == "BEARISH" and bull_hits >= 2 and bear_hits == 0:
        return CriticVerdict(
            severity="warn",
            note=(f"BEARISH call but {bull_hits} of "
                  f"{len(drivers)} key drivers read bullish — "
                  f"downgraded conviction one notch."),
        )
    return None


def _check_stop_target_geometry(pred: dict, entry: float | None) -> CriticVerdict | None:
    """Stop and target should bracket the entry on the correct side.

    BULLISH call → stop < entry < target
    BEARISH call → target < entry < stop
    """
    if entry is None or entry <= 0:
        return None
    stop = pred.get("suggested_stop_pkr")
    target = pred.get("suggested_target_pkr")
    if stop is None or target is None:
        return None
    d = (pred.get("direction") or "").upper()
    try:
        stop = float(stop); target = float(target)
    except (TypeError, ValueError):
        return None
    if d == "BULLISH" and not (stop < entry < target):
        return CriticVerdict(
            severity="fail",
            note=(f"BULLISH call but stop={stop} / entry={entry} / "
                  f"target={target} are not in the order "
                  f"stop < entry < target."),
        )
    if d == "BEARISH" and not (target < entry < stop):
        return CriticVerdict(
            severity="fail",
            note=(f"BEARISH call but target={target} / entry={entry} "
                  f"/ stop={stop} are not in the order "
                  f"target < entry < stop."),
        )
    return None


def _check_synthesizer_alignment(pred: dict, sym: str) -> CriticVerdict | None:
    """Cross-check the LLM's direction against the deterministic
    seven-lens synthesizer in :mod:`brain.verdict_synthesizer`.

    If the LLM is BULLISH but the deterministic synthesizer scores the
    name strongly bearish (score <= -3 with the action AVOID/TRIM), the
    LLM is overruled to a soft cap. The reverse also holds.
    """
    try:
        from brain.verdict_synthesizer import synthesize
        v = synthesize(sym)
    except Exception:
        return None
    if not v or v.get("error"):
        return None
    d_llm = (pred.get("direction") or "").upper()
    score = v.get("score") or 0
    action = v.get("action") or ""

    # Strong disagreement: LLM bullish, synthesizer says AVOID.
    if d_llm == "BULLISH" and action in ("AVOID", "TRIM") and score <= -3:
        return CriticVerdict(
            severity="warn",
            note=(f"LLM is BULLISH but the seven-lens synthesizer "
                  f"verdict is {action} (score {score:+d}). "
                  f"Conviction downgraded one notch; analyst should "
                  f"open the Bot's Verdict panel for the breakdown."),
        )
    if d_llm == "BEARISH" and action in ("BUY", "ADD") and score >= +3:
        return CriticVerdict(
            severity="warn",
            note=(f"LLM is BEARISH but the seven-lens synthesizer "
                  f"verdict is {action} (score {score:+d}). "
                  f"Conviction downgraded one notch; analyst should "
                  f"open the Bot's Verdict panel for the breakdown."),
        )
    return None


def _check_market_regime(pred: dict) -> CriticVerdict | None:
    """Cap any HIGH-conviction BULLISH call when KSE-100 is in a
    confirmed downtrend.

    Closes Gap #3 from the April 29 scorecard: the bot issued
    HIGH-conviction BUYs on NPL, PPL and PSO that morning while the
    KSE-100 had printed three consecutive red sessions and was sitting
    below its 20-day SMA. A confirmed broad-market downtrend is a
    well-established Bayesian prior — even genuinely bullish setups
    underperform inside one — so we soft-cap conviction to MEDIUM and
    surface the regime context to the analyst.

    Conditions (all must hold):
      * 5-day KSE-100 return <= -1.5%
      * Today's close < 20-day simple moving average

    The check is a ``warn`` (one-notch conviction cut), never a ``fail``
    — the strategist can still publish the call, just at lower size.
    """
    d = (pred.get("direction") or "").upper()
    conv = (pred.get("conviction") or "").upper()
    if d != "BULLISH" or conv != "HIGH":
        return None

    if not KSE100_PATH.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(KSE100_PATH)
    except Exception:
        return None
    if df.empty or "kse100_close" not in df.columns:
        return None
    df = df.sort_values("date").tail(30)
    if len(df) < 21:
        return None

    closes = df["kse100_close"].astype(float).reset_index(drop=True)
    today_close = float(closes.iloc[-1])
    five_ago = float(closes.iloc[-6]) if len(closes) >= 6 else None
    sma20 = float(closes.iloc[-21:-1].mean())

    if five_ago is None or five_ago <= 0:
        return None
    ret_5d = (today_close / five_ago) - 1.0

    if ret_5d <= -0.015 and today_close < sma20:
        return CriticVerdict(
            severity="warn",
            note=(f"KSE-100 confirmed downtrend ("
                  f"5d {ret_5d*100:+.1f}%, close "
                  f"{today_close:.0f} below 20d SMA "
                  f"{sma20:.0f}); HIGH-conviction BUYs "
                  f"underperform inside broad-market downtrends, "
                  f"conviction capped at MEDIUM."),
        )
    return None


def _apply_severity(pred: dict, verdict: CriticVerdict) -> None:
    """Mutate the prediction in-place based on the critic's severity."""
    notes = list(pred.get("critic_notes") or [])
    notes.append(f"[{verdict.severity}] {verdict.note}")
    pred["critic_notes"] = notes

    if verdict.severity == "fail":
        pred["direction"] = "NEUTRAL"
        pred["suggested_action"] = "HOLD"
        pred["conviction"] = "LOW"
    elif verdict.severity == "warn":
        downgrade = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}
        pred["conviction"] = downgrade.get(pred.get("conviction"),
                                              pred.get("conviction"))


def review(pred: dict, sym: str, entry: float | None) -> dict:
    """Run all checks against a prediction. Mutates the dict in place
    and returns it for ergonomic chaining.

    The checks run in order — a ``fail`` from one check still allows
    later checks to run, so the analyst sees every issue.
    """
    if not pred:
        return pred

    checks = [
        _check_direction_action_consistency(pred),
        _check_drivers_match_direction(pred),
        _check_stop_target_geometry(pred, entry),
        _check_synthesizer_alignment(pred, sym),
        _check_market_regime(pred),
    ]
    for v in checks:
        if v is not None:
            _apply_severity(pred, v)

    return pred
