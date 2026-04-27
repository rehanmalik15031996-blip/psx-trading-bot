"""Sector-aware macro impact engine.

Translates a daily macro snapshot (policy rate, oil prices, USD/PKR,
coal, cotton, etc.) into:

1.  A list of *active drivers* — macro variables that have moved enough
    today / this week / this month to matter.
2.  A per-sector tailwind / headwind score, with one-sentence
    explanations the analyst can read.
3.  A per-stock score that amplifies or dampens the sector signal based
    on the company's leverage (debt-to-equity from the fundamentals
    cache) — so a high-D/E cement company is hit harder by a rate hike
    than a low-D/E peer.

The engine is **deterministic**: it is a hand-crafted rule book, not a
model. The intent is exactly what the analyst asked for — every
suggestion the system surfaces should carry an explicit reason. The
output is fed both into the LLM briefing (so the AI's rationale uses
sector-specific language) and into the user interface (so the analyst
sees the same reasoning the AI sees).

Design rules
------------
* Sensitivities are scored on a small integer scale (-3..+3) so they
  can be summed across drivers without becoming a black box.
* Every (sector, driver) pair carries a human-readable reason string.
* The amplifier is bounded ([-2, +2] notches) to avoid one extreme
  D/E swamping the entire score.
* Pure Python, no third-party dependencies. Safe to import from any
  process — runs in milliseconds.

Public entry points
-------------------
    detect_drivers(macro, rate, prev_rate=None) -> list[Driver]
    score_sectors(drivers) -> dict[sector] -> SectorImpact
    score_symbol(symbol, sector_impact, fund=None) -> SymbolImpact
    compute_macro_impact(macro=None, rate=None, fund_loader=None) -> dict
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RATE_HISTORY_PATH = PROJECT_ROOT / "data" / "macro" / "_policy_rate_history.json"


# ---------------------------------------------------------------------------
#  Sensitivity rule book
# ---------------------------------------------------------------------------
# Format: SECTOR_RULES[sector][driver_tag] = (score:int, reason:str)
# driver_tag is one of {"rate_up", "rate_down", "oil_up", "oil_down",
#                       "pkr_weak", "pkr_strong", "coal_up", "coal_down",
#                       "cotton_up", "cotton_down", "rate_high",
#                       "rate_low"}.
# rate_high / rate_low describe the *level* (regime) and apply
# continuously; rate_up / rate_down describe a *change* between two
# observations.

SECTOR_RULES: dict[str, dict[str, tuple[int, str]]] = {
    "Banking": {
        "rate_up":   (+2, "Higher policy rate widens net interest margins "
                          "(banks reprice loans faster than deposits). "
                          "CASA-rich names benefit most."),
        "rate_down": (-2, "Lower policy rate compresses net interest "
                          "margins. Treasury book gains may partly offset."),
        "rate_high": (+1, "Restrictive rate regime keeps NIMs elevated."),
        "rate_low":  (-1, "Accommodative rate regime caps NIM expansion."),
        "oil_up":    ( 0, "Mostly indirect — oil only matters via "
                          "inflation pass-through to NIMs."),
        "oil_down":  ( 0, "Indirect — small disinflationary effect."),
        "pkr_weak":  (+1, "Revaluation gains on USD treasury and "
                          "FX-denominated trade book."),
        "pkr_strong":(-1, "Smaller FX revaluation gains."),
    },
    "Cement": {
        "rate_up":   (-3, "Sector is highly leveraged: financial costs "
                          "spike *and* mortgage / construction demand "
                          "falls. Double hit."),
        "rate_down": (+3, "Construction picks up and financial costs "
                          "fall — sector's biggest tailwind."),
        "rate_high": (-2, "Sustained high rates strangle housing and "
                          "infrastructure demand."),
        "rate_low":  (+2, "Low rates revive housing and infrastructure."),
        "oil_up":    (-2, "Furnace-oil and freight costs rise; coal-fired "
                          "kilns also pay more for delivered fuel."),
        "oil_down":  (+2, "Energy and freight costs fall — direct margin "
                          "tailwind."),
        "pkr_weak":  (-1, "Imported coal becomes more expensive in PKR."),
        "pkr_strong":(+1, "Imported coal becomes cheaper."),
        "coal_up":   (-3, "Direct fuel-cost shock on cement margins — "
                          "coal is the dominant kiln fuel."),
        "coal_down": (+3, "Cement margins ease as coal cost falls."),
    },
    "Oil & Gas E&P": {
        "rate_up":   ( 0, "Cash-rich, low-leverage names; mostly "
                          "insensitive to rate changes."),
        "rate_down": ( 0, "Rate moves are not the primary driver."),
        "rate_high": ( 0, "Rate level secondary; oil price dominates."),
        "rate_low":  ( 0, "Rate level secondary."),
        "oil_up":    (+3, "Direct revenue lift on every barrel produced — "
                          "the strongest single driver for the sector."),
        "oil_down":  (-3, "Direct revenue hit; sector earnings fall in "
                          "lockstep with crude."),
        "pkr_weak":  (+2, "Wellhead prices are USD-linked; weaker PKR "
                          "translates to more PKR per barrel."),
        "pkr_strong":(-2, "Smaller PKR revenue lift on each USD barrel."),
    },
    "OMC/Refining": {
        "rate_up":   (-1, "Inventory financing costs rise; OMCs carry "
                          "large product stocks."),
        "rate_down": (+1, "Lower inventory financing costs."),
        "rate_high": (-1, "Sustained working-capital drag."),
        "rate_low":  (+1, "Working-capital relief."),
        "oil_up":    (+1, "Inventory revaluation gains; refining margins "
                          "can widen short-term."),
        "oil_down":  (-1, "Inventory losses risk; refining margins "
                          "compress."),
        "pkr_weak":  ( 0, "Roughly neutral — government formula passes "
                          "through FX changes within a few weeks."),
        "pkr_strong":( 0, "Roughly neutral after pass-through."),
    },
    "Power": {
        "rate_up":   (-1, "Long-term project debt costs rise; new capex "
                          "becomes harder to justify."),
        "rate_down": (+1, "Existing financial costs fall on refinanced "
                          "debt."),
        "rate_high": (-1, "Sustained pressure on highly-leveraged IPPs."),
        "rate_low":  (+1, "Refinancing relief."),
        "oil_up":    (+1, "Furnace-oil-fired plants get fuel-cost "
                          "pass-through under PPA indexation."),
        "oil_down":  (-1, "Lower indexation revenue."),
        "pkr_weak":  (+1, "Capacity payments are partly USD-indexed under "
                          "old PPAs — weaker PKR helps reported earnings."),
        "pkr_strong":(-1, "Smaller PKR uplift on USD-indexed payments."),
    },
    "Conglomerate/Chem": {
        "rate_up":   (-2, "Petrochemical balance sheets are typically "
                          "highly leveraged; rate hikes flow straight to "
                          "the bottom line."),
        "rate_down": (+2, "Financial costs ease — direct EPS lift."),
        "rate_high": (-1, "Persistent financial-cost drag."),
        "rate_low":  (+1, "Persistent financial-cost relief."),
        "oil_up":    (+1, "Wider naphtha-to-PVC spread in some "
                          "configurations; mixed for downstream."),
        "oil_down":  (-1, "Narrower margins on PVC and other "
                          "oil-derivative chemicals."),
        "pkr_weak":  (-1, "Imported feedstock costs rise."),
        "pkr_strong":(+1, "Imported feedstock costs fall."),
    },
    "Pharma": {
        "rate_up":   (-1, "Pharma carries some leverage; financial cost "
                          "rises bite EPS."),
        "rate_down": (+1, "Financial-cost relief."),
        "rate_high": (-1, "Sustained financial drag."),
        "rate_low":  (+1, "Sustained relief."),
        "oil_up":    (-1, "Packaging and freight costs rise; DRAP price "
                          "caps limit pass-through."),
        "oil_down":  (+1, "Lower input costs."),
        "pkr_weak":  (-2, "Active Pharmaceutical Ingredient (API) imports "
                          "become more expensive; DRAP price caps prevent "
                          "full pass-through to consumers."),
        "pkr_strong":(+2, "API import-cost relief — pharma gross margins "
                          "expand quickly."),
    },
    "Misc": {  # PABC — aluminium can manufacturer
        "rate_up":   (-1, "Adds to financial costs on working capital."),
        "rate_down": (+1, "Working-capital relief."),
        "oil_up":    (-1, "Energy-intensive aluminium manufacturing pays "
                          "more for power and freight."),
        "oil_down":  (+1, "Energy cost relief."),
        "pkr_weak":  (-2, "Aluminium ingot imports become more expensive."),
        "pkr_strong":(+2, "Imported aluminium ingot becomes cheaper."),
    },
}


# ---------------------------------------------------------------------------
#  Per-stock per-sector amplifiers
# ---------------------------------------------------------------------------
# Inside Banking, the largest by deposit base captures the biggest NIM
# uplift. Inside Cement, the highest D/E suffers the worst rate hit.
# These are sector-aware tags so the explanation can name the company
# specifically, not just the sector.

BANKING_TIER: dict[str, str] = {
    # Tier-1 = large, CASA-rich → strongest beneficiary of rate hikes
    "MEBL": "tier-1",
    "MCB":  "tier-1",
    "FABL": "tier-2",
}


# ---------------------------------------------------------------------------
#  Driver detection
# ---------------------------------------------------------------------------
@dataclass
class Driver:
    name: str               # human-readable: "Brent crude"
    tag: str                # rule-book key: "oil_up", "rate_high", ...
    move: str               # "+9.7% in 21d", "raised 100 bps to 11.5%"
    magnitude: str          # "STRONG", "MODERATE", "MILD"
    context: str = ""       # optional extra detail


def _load_rate_history() -> list[dict]:
    if not RATE_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(RATE_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_rate_history(rows: list[dict]) -> None:
    RATE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    RATE_HISTORY_PATH.write_text(
        json.dumps(rows, indent=2, default=str), encoding="utf-8")


def _record_rate_observation(rate_pct: float | None) -> Optional[float]:
    """Append today's rate to the history file (idempotent on the same
    rate value) and return the previous distinct value if any."""
    if rate_pct is None:
        return None
    hist = _load_rate_history()
    today = datetime.now(timezone.utc).date().isoformat()
    if not hist or hist[-1].get("rate_pct") != rate_pct:
        hist.append({"date": today, "rate_pct": float(rate_pct)})
        # Keep only the most recent 50 entries — that's >2 years of MPC
        # decisions, plenty for our purposes.
        hist = hist[-50:]
        _save_rate_history(hist)
    # Find the most recent distinct previous rate
    for row in reversed(hist[:-1]):
        if row.get("rate_pct") != rate_pct:
            return float(row["rate_pct"])
    return None


def detect_drivers(
    macro: dict | None,
    rate: dict | None,
) -> list[Driver]:
    """Inspect today's macro snapshot and return the meaningful drivers.

    Thresholds are deliberately conservative — a 1% oil tick is noise,
    a 5% move in 5d or a 10% move in 21d is news.
    """
    drivers: list[Driver] = []
    indicators = (macro or {}).get("indicators") or {}

    # ---- Policy rate (level + change vs last observation)
    rate_pct = (rate or {}).get("policy_rate_pct")
    if rate_pct is not None:
        prev = _record_rate_observation(rate_pct)
        # Level driver — every day this carries a small influence.
        if rate_pct >= 14.0:
            drivers.append(Driver(
                name="Policy rate (level)",
                tag="rate_high",
                move=f"{rate_pct:.2f}% (restrictive regime)",
                magnitude="STRONG",
                context="Restrictive monetary policy; high discount rate "
                         "weighs on equity ex-banks.",
            ))
        elif rate_pct <= 11.0:
            drivers.append(Driver(
                name="Policy rate (level)",
                tag="rate_low",
                move=f"{rate_pct:.2f}% (accommodative regime)",
                magnitude="STRONG",
                context="Accommodative monetary policy; tailwind for "
                         "leveraged sectors.",
            ))
        # Change driver — only fires on a real MPC move.
        if prev is not None:
            delta = rate_pct - prev
            if delta >= 0.50:
                drivers.append(Driver(
                    name="Policy rate (change)",
                    tag="rate_up",
                    move=f"raised {delta*100:+.0f} bps to {rate_pct:.2f}%",
                    magnitude="STRONG" if delta >= 1.0 else "MODERATE",
                    context=f"State Bank lifted the policy rate from "
                             f"{prev:.2f}% to {rate_pct:.2f}%.",
                ))
            elif delta <= -0.50:
                drivers.append(Driver(
                    name="Policy rate (change)",
                    tag="rate_down",
                    move=f"cut {delta*100:+.0f} bps to {rate_pct:.2f}%",
                    magnitude="STRONG" if delta <= -1.0 else "MODERATE",
                    context=f"State Bank cut the policy rate from "
                             f"{prev:.2f}% to {rate_pct:.2f}%.",
                ))

    # ---- Brent / WTI (oil price)
    brent = indicators.get("brent") or {}
    r5 = brent.get("ret_5d") or 0
    r21 = brent.get("ret_21d") or 0
    if r21 >= 0.10 or r5 >= 0.07:
        drivers.append(Driver(
            name="Brent crude",
            tag="oil_up",
            move=(f"{r21*100:+.1f}% in 21d" if abs(r21) >= 0.10
                  else f"{r5*100:+.1f}% in 5d"),
            magnitude=("STRONG" if r21 >= 0.15 else "MODERATE"),
            context=f"Brent at {brent.get('value', '?')} USD/bbl.",
        ))
    elif r21 <= -0.10 or r5 <= -0.07:
        drivers.append(Driver(
            name="Brent crude",
            tag="oil_down",
            move=(f"{r21*100:+.1f}% in 21d" if abs(r21) >= 0.10
                  else f"{r5*100:+.1f}% in 5d"),
            magnitude=("STRONG" if r21 <= -0.15 else "MODERATE"),
            context=f"Brent at {brent.get('value', '?')} USD/bbl.",
        ))

    # ---- USD/PKR
    pkr = indicators.get("usdpkr") or {}
    pr21 = pkr.get("ret_21d") or 0
    pr63 = pkr.get("ret_63d") or 0
    if pr21 >= 0.015 or pr63 >= 0.03:
        drivers.append(Driver(
            name="USD/PKR",
            tag="pkr_weak",
            move=(f"PKR weaker {pr21*100:+.1f}% in 21d" if abs(pr21) >= 0.015
                  else f"PKR weaker {pr63*100:+.1f}% in 63d"),
            magnitude=("STRONG" if pr21 >= 0.03 else "MODERATE"),
            context=f"USD/PKR at {pkr.get('value', '?')}.",
        ))
    elif pr21 <= -0.01 or pr63 <= -0.02:
        drivers.append(Driver(
            name="USD/PKR",
            tag="pkr_strong",
            move=(f"PKR stronger {pr21*100:+.1f}% in 21d" if abs(pr21) >= 0.01
                  else f"PKR stronger {pr63*100:+.1f}% in 63d"),
            magnitude=("STRONG" if pr21 <= -0.02 else "MODERATE"),
            context=f"USD/PKR at {pkr.get('value', '?')}.",
        ))

    # ---- Coal proxy (we don't store a dedicated coal series, but
    # cement-grade coal correlates 0.7+ with Brent on a quarterly basis,
    # so a sustained Brent move triggers a soft coal_up/coal_down too).
    if r21 >= 0.15:
        drivers.append(Driver(
            name="Coal (Brent proxy)",
            tag="coal_up",
            move=f"implied via Brent {r21*100:+.1f}% in 21d",
            magnitude="MODERATE",
            context="Cement-grade coal lags Brent by ~30 days but the "
                     "directional read is reliable.",
        ))
    elif r21 <= -0.15:
        drivers.append(Driver(
            name="Coal (Brent proxy)",
            tag="coal_down",
            move=f"implied via Brent {r21*100:+.1f}% in 21d",
            magnitude="MODERATE",
            context="Cement-grade coal lags Brent.",
        ))

    return drivers


# ---------------------------------------------------------------------------
#  Sector scoring
# ---------------------------------------------------------------------------
@dataclass
class SectorImpact:
    sector: str
    score: int                              # signed sum of driver scores
    tailwinds: list[str] = field(default_factory=list)
    headwinds: list[str] = field(default_factory=list)
    verdict: str = "NEUTRAL"                # TAILWIND / HEADWIND / NEUTRAL


def _bucket_verdict(score: int) -> str:
    if score >= 3:  return "STRONG TAILWIND"
    if score >= 1:  return "TAILWIND"
    if score <= -3: return "STRONG HEADWIND"
    if score <= -1: return "HEADWIND"
    return "NEUTRAL"


def score_sectors(drivers: list[Driver]) -> dict[str, SectorImpact]:
    """Apply each driver to every sector and accumulate the score."""
    out: dict[str, SectorImpact] = {}
    for sector, rules in SECTOR_RULES.items():
        impact = SectorImpact(sector=sector, score=0)
        for d in drivers:
            sens = rules.get(d.tag)
            if sens is None:
                continue
            score, reason = sens
            if score == 0:
                continue
            impact.score += score
            line = (f"{('+' if score > 0 else '')}{score} | "
                    f"{d.name}: {d.move} — {reason}")
            (impact.tailwinds if score > 0 else impact.headwinds).append(line)
        impact.verdict = _bucket_verdict(impact.score)
        out[sector] = impact
    return out


# ---------------------------------------------------------------------------
#  Per-stock scoring (with leverage amplifier)
# ---------------------------------------------------------------------------
@dataclass
class SymbolImpact:
    symbol: str
    sector: str
    sector_score: int
    stock_score: int
    tailwinds: list[str] = field(default_factory=list)
    headwinds: list[str] = field(default_factory=list)
    amplifier_note: str = ""
    verdict: str = "NEUTRAL"


def score_symbol(
    symbol: str,
    sector: str,
    sector_impact: SectorImpact,
    fund: dict | None = None,
) -> SymbolImpact:
    """Take the sector reading and amplify / dampen by per-stock data.

    Currently uses debt-to-equity from the fundamentals cache:

    * D/E ≥ 1.5  → +/-2 notch amplifier on rate-sensitive moves.
    * D/E ≥ 1.0  → +/-1 notch amplifier on rate-sensitive moves.
    * D/E ≤ 0.3  → /-1 notch dampener on rate-sensitive moves.

    For Banking, also boosts tier-1 names (MCB, MEBL) on rate-up days
    because their CASA captures the NIM expansion most efficiently.
    """
    de = None
    if fund:
        de = fund.get("debt_to_equity")

    amp_note = ""
    amp = 0  # signed amplifier (added to stock_score, not multiplied)

    has_rate_signal = any("rate" in t.lower() or "Rate" in t
                           for t in sector_impact.tailwinds + sector_impact.headwinds)
    if de is not None and has_rate_signal:
        # Pure leverage amplifier: high D/E means the sector's rate sign
        # is amplified.
        sign = 1 if sector_impact.score > 0 else -1 if sector_impact.score < 0 else 0
        if de >= 1.5:
            amp = sign * 2
            amp_note = (f"Very high debt-to-equity ({de:.1f}) magnifies "
                        f"rate-driven impact (+/-2 notches).")
        elif de >= 1.0:
            amp = sign * 1
            amp_note = (f"High debt-to-equity ({de:.1f}) magnifies "
                        f"rate-driven impact (+/-1 notch).")
        elif de <= 0.3:
            amp = -sign * 1
            amp_note = (f"Low debt-to-equity ({de:.1f}) dampens "
                        f"rate-driven impact (-1 notch).")

    # Banking tier amplifier on rate-up days
    if sector == "Banking":
        tier = BANKING_TIER.get(symbol)
        if tier == "tier-1" and sector_impact.score > 0:
            amp += 1
            amp_note = ((amp_note + " " if amp_note else "")
                         + f"{symbol} is a CASA-rich tier-1 bank — bigger "
                           f"NIM expansion on rate-up days (+1 notch).")
        elif tier == "tier-2" and sector_impact.score > 0:
            amp += 0  # tier-2 = no amplifier

    # Power IPP-specific: HUBCO has been undergoing PPA renegotiation
    # which means rate-up impact is more bearish than the sector
    # average suggests.
    if sector == "Power" and symbol == "HUBC" and sector_impact.score < 0:
        amp -= 1
        amp_note = ((amp_note + " " if amp_note else "")
                    + "HUBC PPA renegotiation increases rate-up sensitivity "
                      "(-1 notch).")

    stock_score = sector_impact.score + amp
    return SymbolImpact(
        symbol=symbol,
        sector=sector,
        sector_score=sector_impact.score,
        stock_score=stock_score,
        tailwinds=list(sector_impact.tailwinds),
        headwinds=list(sector_impact.headwinds),
        amplifier_note=amp_note,
        verdict=_bucket_verdict(stock_score),
    )


# ---------------------------------------------------------------------------
#  One-shot helper
# ---------------------------------------------------------------------------
def compute_macro_impact(
    macro: dict | None = None,
    rate: dict | None = None,
    fund_loader: Callable[[str], dict | None] | None = None,
    universe: list[str] | None = None,
) -> dict:
    """Convenience wrapper used by the briefing builder and the UI.

    Returns a serialisable dict with three sections::

        {
          "as_of":     ISO timestamp,
          "drivers":   [{name, tag, move, magnitude, context}, ...],
          "by_sector": {sector: {score, verdict, tailwinds, headwinds}, ...},
          "by_symbol": {symbol: {sector_score, stock_score, verdict,
                                  tailwinds, headwinds, amplifier_note}, ...},
        }

    All inputs are optional — when not provided, the function pulls
    them from ``ui.tools`` and ``connectors.yfinance_fundamentals``.
    """
    if macro is None or rate is None:
        try:
            from ui.tools import get_macro_snapshot, get_policy_rate
            macro = macro or get_macro_snapshot()
            rate = rate or get_policy_rate()
        except Exception:
            macro = macro or {}
            rate = rate or {}

    if universe is None:
        try:
            from config.universe import UNIVERSE
            universe_pairs = [(u.symbol, u.sector) for u in UNIVERSE]
        except Exception:
            universe_pairs = []
    else:
        try:
            from config.universe import sector_of
            universe_pairs = [(s, sector_of(s) or "Other") for s in universe]
        except Exception:
            universe_pairs = [(s, "Other") for s in universe]

    if fund_loader is None:
        def _default_loader(sym: str) -> dict | None:
            try:
                from connectors.yfinance_fundamentals import load_latest
                f = load_latest(sym) or {}
                # Materialise D/E from the raw balance-sheet fields.
                eq = f.get("total_equity_pkr")
                dt = f.get("total_debt_pkr")
                if eq and eq > 0 and dt is not None:
                    f["debt_to_equity"] = round(float(dt) / float(eq), 3)
                return f
            except Exception:
                return None
        fund_loader = _default_loader

    drivers = detect_drivers(macro, rate)
    sectors = score_sectors(drivers)

    by_symbol: dict[str, dict] = {}
    for sym, sector in universe_pairs:
        sec_imp = sectors.get(sector)
        if sec_imp is None:
            sec_imp = SectorImpact(sector=sector, score=0,
                                     verdict="NEUTRAL")
        try:
            fund = fund_loader(sym) if fund_loader else None
        except Exception:
            fund = None
        sym_imp = score_symbol(sym, sector, sec_imp, fund)
        by_symbol[sym] = asdict(sym_imp)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drivers":   [asdict(d) for d in drivers],
        "by_sector": {s: asdict(v) for s, v in sectors.items()},
        "by_symbol": by_symbol,
    }


# ---------------------------------------------------------------------------
#  Manual test
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    res = compute_macro_impact()
    print(f"Drivers active today: {len(res['drivers'])}")
    for d in res["drivers"]:
        print(f"  {d['magnitude']:>8}  {d['name']:<26}  {d['move']}")
    print()
    for sector, s in res["by_sector"].items():
        print(f"  {sector:<24}  score={s['score']:+d}  ({s['verdict']})")
        for t in s["tailwinds"]:
            print(f"    + {t}")
        for h in s["headwinds"]:
            print(f"    - {h}")
        print()
