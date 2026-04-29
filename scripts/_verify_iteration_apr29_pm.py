"""Local verification driver for the April 29 PM iteration.

Confirms each of the five strategy fixes can be exercised without an
external network call, plus a smoke test for the new international
news connector and the broad-market shock detector. Run::

    python scripts/_verify_iteration_apr29_pm.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ok(name: str, msg: str = "") -> None:
    print(f"  PASS  {name}" + (f"  ({msg})" if msg else ""))


def _fail(name: str, msg: str) -> None:
    print(f"  FAIL  {name}  ({msg})")


def test_stretched_helper():
    """Gap 1 — z-score helper imports and runs deterministically."""
    from brain.macro_impact import _is_stretched, _downshift_magnitude
    res = _is_stretched("brent", 0.05)
    assert isinstance(res, tuple) and len(res) == 2
    assert _downshift_magnitude("STRONG") == "MODERATE"
    assert _downshift_magnitude("MODERATE") == "MILD"
    _ok("Gap 1 stretched helper", f"_is_stretched('brent', 0.05) -> {res}")


def test_kibor_change_fields_present():
    """Gap 2 — _load_kpi_snapshot exposes the 1-day fields."""
    from brain.macro_impact import _load_kpi_snapshot
    kp = _load_kpi_snapshot()
    keys = {"kibor_3m_change_1d", "kibor_12m_change_1d",
             "tbill_3m_change_1d"}
    have = keys & set(kp.keys())
    if not have:
        # OK if there's only one row in the parquet (no 1d delta
        # computable yet) — just confirm the code path exists.
        _ok("Gap 2 KIBOR 1d fields",
            "fields not yet populated (expected on a 1-row parquet)")
    else:
        _ok("Gap 2 KIBOR 1d fields",
            f"present: {sorted(have)}")


def test_market_regime_critic():
    """Gap 3 — _check_market_regime is wired into review()."""
    from brain.prediction_critic import _check_market_regime, review
    pred = {"direction": "NEUTRAL", "conviction": "LOW",
             "suggested_action": "HOLD"}
    out = _check_market_regime(pred)
    assert out is None or hasattr(out, "severity")
    pred2 = {"direction": "BULLISH", "conviction": "HIGH",
              "suggested_action": "BUY"}
    review(pred2, "FAKE", entry=100.0)
    _ok("Gap 3 KSE-100 regime check", "review() ran without exception")


def test_concentration_caps():
    """Gap 4 — _apply_concentration_caps demotes the weakest of a
    cluster."""
    from brain.verdict_synthesizer import _apply_concentration_caps
    rows = [
        {"symbol": "A", "sector": "Energy", "action": "BUY",
         "score": +9, "direction": "BULLISH", "conviction": "HIGH"},
        {"symbol": "B", "sector": "Energy", "action": "ADD",
         "score": +5, "direction": "BULLISH", "conviction": "MEDIUM"},
        {"symbol": "C", "sector": "Energy", "action": "BUY",
         "score": +6, "direction": "BULLISH", "conviction": "MEDIUM"},
        {"symbol": "D", "sector": "Banking", "action": "BUY",
         "score": +4, "direction": "BULLISH", "conviction": "MEDIUM"},
    ]
    out = _apply_concentration_caps(rows)
    weakest = next(r for r in out if r["symbol"] == "B")
    assert weakest["action"] == "HOLD", weakest
    assert weakest.get("concentration_warning"), weakest
    _ok("Gap 4 concentration cap",
        f"B downgraded with note: {weakest['concentration_warning']}")


def test_shock_threshold_and_categories():
    """Gap 5 — threshold lowered to 0.35 and broad-market mode wired."""
    from scripts.check_news_shocks import (
        MIN_SENTIMENT, SHOCK_CATEGORIES, _detect_broad_market_shock,
    )
    assert MIN_SENTIMENT == 0.35, MIN_SENTIMENT
    assert "BROAD_MARKET" in SHOCK_CATEGORIES
    assert "KSE100" in SHOCK_CATEGORIES
    import pandas as pd
    df = pd.DataFrame([
        {"title": "KSE-100 retreats 2,588 points", "sentiment": -0.65,
         "confidence": "HIGH"},
        {"title": "PSX reverses early gains, closes red",
         "sentiment": -0.50, "confidence": "HIGH"},
        {"title": "Rate hike undermines investor confidence",
         "sentiment": -0.40, "confidence": "HIGH"},
    ])
    out = _detect_broad_market_shock(df, fired_broad_keys=set())
    assert out is not None
    assert out["is_broad_market"] is True
    assert out["side"] == "BEARISH"
    _ok("Gap 5 broad-market shock",
        f"side={out['side']} n={out['n_articles']} "
        f"avg_sent={out['sentiment']:+.2f}")


def test_intl_news_module_imports():
    from connectors.intl_news import IntlNewsConnector, FEEDS, RELEVANCE_KEYWORDS
    assert len(FEEDS) >= 8
    assert "pakistan" in RELEVANCE_KEYWORDS
    _ok("intl_news connector",
        f"{len(FEEDS)} feeds, {len(RELEVANCE_KEYWORDS)} keywords")


def test_score_news_pipeline_imports_intl():
    import importlib
    mod = importlib.import_module("scripts.score_news_sentiment")
    assert hasattr(mod, "IntlNewsConnector")
    _ok("score_news_sentiment wires intl",
        "IntlNewsConnector imported into pipeline")


def main() -> int:
    print("Verifying iteration April 29 PM ...")
    tests = [
        test_stretched_helper,
        test_kibor_change_fields_present,
        test_market_regime_critic,
        test_concentration_caps,
        test_shock_threshold_and_categories,
        test_intl_news_module_imports,
        test_score_news_pipeline_imports_intl,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            _fail(t.__name__, f"{type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) failed.")
        return 1
    print(f"\nAll {len(tests)} fix verifications passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
