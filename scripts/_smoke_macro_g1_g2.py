"""Smoke test for the 2026-05-14 macro-audit additions:

  G-1 - BTC risk-on/risk-off driver
  G-2 - Brent-WTI geopolitical_oil_premium driver

We construct synthetic ``indicators`` payloads that should unambiguously
fire each new driver and assert the tags appear. We also assert the
"happy path" (small moves) does NOT fire any of the new tags.

This test deliberately uses an empty ``kpis={}`` so we don't depend on
the on-disk KPI snapshot, and a None ``rate`` so we don't trigger the
rate side-channels.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from brain.macro_impact import detect_drivers, SECTOR_RULES  # noqa: E402


def _tags(drivers) -> set[str]:
    return {d.tag for d in drivers}


def _run(name: str, macro: dict, expect: set[str], reject: set[str]) -> bool:
    drivers = detect_drivers(macro=macro, rate=None, kpis={})
    tags = _tags(drivers)
    missing = expect - tags
    extra   = reject & tags
    ok = not missing and not extra
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}")
    print(f"    drivers: {sorted(tags) or '(none)'}")
    if missing:
        print(f"    MISSING: {sorted(missing)}")
    if extra:
        print(f"    UNEXPECTED: {sorted(extra)}")
    return ok


def test_btc_risk_off() -> bool:
    macro = {"indicators": {
        "btc":   {"ret_5d": -0.18, "ret_21d": -0.25},
        "brent": {"value": 70.0, "ret_5d": 0.00, "ret_21d": 0.00},
        "wti":   {"ret_5d": 0.00},
    }}
    return _run("G-1 btc_risk_off fires on -18% in 5d / -25% in 21d",
                macro=macro,
                expect={"btc_risk_off"},
                reject={"btc_risk_on", "geopolitical_oil_premium"})


def test_btc_risk_off_strong() -> bool:
    macro = {"indicators": {
        "btc":   {"ret_5d": -0.25, "ret_21d": -0.35},
        "brent": {"value": 70.0, "ret_5d": 0.00, "ret_21d": 0.00},
        "wti":   {"ret_5d": 0.00},
    }}
    drivers = detect_drivers(macro=macro, rate=None, kpis={})
    tags = _tags(drivers)
    btc = next((d for d in drivers if d.tag == "btc_risk_off"), None)
    ok = btc is not None and btc.magnitude == "STRONG"
    print(f"[{'PASS' if ok else 'FAIL'}] G-1 btc_risk_off is STRONG at -25%/-35%")
    if btc:
        print(f"    magnitude={btc.magnitude}  move={btc.move!r}")
    return ok


def test_btc_risk_on() -> bool:
    macro = {"indicators": {
        "btc":   {"ret_5d": 0.20, "ret_21d": 0.30},
        "brent": {"value": 70.0, "ret_5d": 0.00, "ret_21d": 0.00},
        "wti":   {"ret_5d": 0.00},
    }}
    return _run("G-1 btc_risk_on fires on +20% in 5d / +30% in 21d",
                macro=macro,
                expect={"btc_risk_on"},
                reject={"btc_risk_off", "geopolitical_oil_premium"})


def test_geopolitical_oil_premium() -> bool:
    macro = {"indicators": {
        "btc":   {"ret_5d": 0.00, "ret_21d": 0.00},
        "brent": {"value": 88.0, "ret_5d": 0.08, "ret_21d": 0.05},
        "wti":   {"ret_5d": 0.03},   # spread = 5pp (>=4pp = STRONG)
    }}
    drivers = detect_drivers(macro=macro, rate=None, kpis={})
    tags = _tags(drivers)
    geop = next((d for d in drivers if d.tag == "geopolitical_oil_premium"),
                None)
    ok = geop is not None and geop.magnitude == "STRONG"
    print(f"[{'PASS' if ok else 'FAIL'}] G-2 geopolitical_oil_premium fires "
          f"STRONG at Brent +8%/WTI +3% (spread 5pp)")
    print(f"    drivers: {sorted(tags) or '(none)'}")
    if geop:
        print(f"    magnitude={geop.magnitude}  move={geop.move!r}")
    return ok


def test_no_premium_when_spread_too_small() -> bool:
    macro = {"indicators": {
        "btc":   {"ret_5d": 0.00, "ret_21d": 0.00},
        "brent": {"value": 88.0, "ret_5d": 0.06, "ret_21d": 0.04},
        "wti":   {"ret_5d": 0.05},   # spread = 1pp, below 2pp threshold
    }}
    return _run("G-2 no premium when Brent-WTI spread <2pp",
                macro=macro,
                expect=set(),
                reject={"geopolitical_oil_premium",
                        "btc_risk_off", "btc_risk_on"})


def test_happy_path_no_new_tags() -> bool:
    macro = {"indicators": {
        "btc":   {"ret_5d": 0.02, "ret_21d": 0.05},
        "brent": {"value": 75.0, "ret_5d": 0.01, "ret_21d": -0.01},
        "wti":   {"ret_5d": 0.005},
    }}
    return _run("happy path: small moves do NOT fire new tags",
                macro=macro,
                expect=set(),
                reject={"btc_risk_off", "btc_risk_on",
                        "geopolitical_oil_premium"})


def test_sector_rules_wired() -> bool:
    needed = {
        "Banking": ["btc_risk_off", "btc_risk_on"],
        "Cement":  ["btc_risk_off", "btc_risk_on"],
        "Oil & Gas E&P": ["geopolitical_oil_premium"],
        "OMC/Refining":  ["geopolitical_oil_premium"],
    }
    missing = []
    for sector, tags in needed.items():
        rules = SECTOR_RULES.get(sector) or {}
        for t in tags:
            if t not in rules:
                missing.append(f"{sector}/{t}")
    ok = not missing
    print(f"[{'PASS' if ok else 'FAIL'}] SECTOR_RULES wired for new tags")
    if missing:
        print(f"    MISSING: {missing}")
    return ok


def main() -> int:
    results = [
        test_btc_risk_off(),
        test_btc_risk_off_strong(),
        test_btc_risk_on(),
        test_geopolitical_oil_premium(),
        test_no_premium_when_spread_too_small(),
        test_happy_path_no_new_tags(),
        test_sector_rules_wired(),
    ]
    failed = sum(1 for r in results if not r)
    total = len(results)
    print()
    print(f"--- {total - failed}/{total} passed ---")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
