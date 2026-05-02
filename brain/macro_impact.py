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
SBP_RATES_PATH   = PROJECT_ROOT / "data" / "macro" / "sbp_rates.parquet"
KSE100_PATH      = PROJECT_ROOT / "data" / "macro" / "kse100.parquet"
CPI_PATH         = PROJECT_ROOT / "data" / "macro" / "cpi_pakistan.parquet"
MACRO_SERIES_DIR = PROJECT_ROOT / "data" / "macro"
# Hand-curated circular-debt events (resolutions / worsening). Empty file
# is fine — the driver simply stays silent. See `_load_circular_debt_events`
# below for the schema.
CIRCULAR_DEBT_PATH = PROJECT_ROOT / "data" / "macro" / "circular_debt_events.json"


# ---------------------------------------------------------------------------
#  Stretched-signal helper
# ---------------------------------------------------------------------------
# Closes Gap #1 from the April 29 scorecard: the engine was reading a
# 9.7% Brent rally as a fresh tailwind even though it sat at the very
# top of its trailing-year distribution and had already begun to
# reverse intraday. ``_is_stretched`` z-scores the current 5-day return
# against the trailing-1-year distribution of 5-day returns. A move
# beyond ±1.5 sigma is statistically extreme and we should distrust
# its directional read.
def _is_stretched(
    series_key: str,
    current_ret_5d: float | None,
    lookback_days: int = 252,
    threshold: float = 1.5,
) -> tuple[bool, float | None]:
    """Return ``(stretched, z_score)`` for a yfinance macro series.

    The function reads ``data/macro/<series_key>.parquet`` (the same
    files refreshed by ``scripts/refresh_macro_series.py``), computes
    rolling 5-day returns over the last ``lookback_days`` business
    days, and checks whether ``current_ret_5d`` lies more than
    ``threshold`` standard deviations from the mean.

    A safe ``(False, None)`` is returned whenever the series is
    missing, too short, or has no variance — we never want a missing
    file to silently dampen drivers.
    """
    if current_ret_5d is None:
        return False, None
    try:
        import pandas as pd
    except Exception:
        return False, None

    p = MACRO_SERIES_DIR / f"{series_key}.parquet"
    if not p.exists():
        return False, None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return False, None
    if df.empty or "value" not in df.columns:
        return False, None

    df = df.sort_values("date").tail(lookback_days + 10)
    closes = df["value"].astype(float)
    rets = closes.pct_change(5).dropna()
    if len(rets) < 30:
        return False, None
    mu = float(rets.mean())
    sd = float(rets.std())
    if sd <= 0:
        return False, None
    z = (float(current_ret_5d) - mu) / sd
    return abs(z) >= threshold, round(z, 2)


def _downshift_magnitude(mag: str) -> str:
    """STRONG → MODERATE → MILD → MILD (clamped)."""
    return {"STRONG": "MODERATE", "MODERATE": "MILD",
            "MILD": "MILD"}.get(mag, mag)


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
        "gold_up":   (-1, "Strong gold = risk-off mood = equity outflow "
                          "from EM banks."),
        "gold_down": (+1, "Risk-on tone supports bank equities."),
        "copper_up": (+1, "Copper strength signals global industrial "
                          "growth — supports loan demand and asset "
                          "quality on emerging-market bank books."),
        "copper_down":(-1,"Copper weakness = global slowdown signal — "
                          "rising NPL risk."),
        # Industry-specific KPIs ------------------------------------
        "tbill_above_policy": (+1, "T-bill 3M trading above the policy "
                                    "rate signals the market expects "
                                    "future hikes — banks lock in higher "
                                    "yields on the new investment book."),
        "tbill_below_policy": (-1, "T-bill 3M trading below the policy "
                                    "rate signals expected cuts — banks "
                                    "see investment yields compress."),
        "tbill_up":   (+1, "Rising T-bill 3M cut-offs lift bank treasury "
                            "yields immediately (the investment book "
                            "reprices faster than deposits)."),
        "tbill_down": (-1, "Falling T-bill 3M cut-offs squeeze bank "
                            "treasury yields immediately."),
        "kibor_up":   (+1, "Higher KIBOR feeds straight into floating-rate "
                            "loan yields — direct NII boost."),
        "kibor_down": (-1, "Lower KIBOR drags floating-rate loan yields."),
        "kibor_shock": (-2, "1-day KIBOR repricing of >=25 bps — even "
                              "though banks benefit from higher yields "
                              "long-term, an MPC-day shock typically "
                              "drags bank equities short-term as "
                              "leverage-heavy borrowers (cement / "
                              "power) sell off and drag the index."),
        "reserves_stress":  (-2, "FX-reserve stress triggers IMF / SBP "
                                  "tightening risk — bank equities sell "
                                  "off ahead of currency moves."),
        "reserves_recovery":(+1, "Rebuilding reserves de-risks the BoP — "
                                  "supportive for bank equities."),
        "kse100_up":   (+1, "Broad market strength supports bank equities "
                             "via fund flows and trading book gains."),
        "kse100_down": (-1, "Broad market weakness pressures bank equities."),
        "cpi_high":    (+1, "Sticky inflation forces SBP to hold high — "
                             "NIMs stay elevated."),
        "cpi_easing":  (-1, "Cooling CPI opens the door to rate cuts — "
                             "NIMs face compression risk."),
        # Circular-debt events ---------------------------------------
        # Net positive: banks are paid a guaranteed lower lending rate
        # (~150 bps below their normal book) on the new financing, which
        # is a small drag on incremental NIM, but the de-risking of their
        # legacy power-sector exposure (Rs 660 bn restructured in the
        # Dec-2025 deal) and the freeing-up of sovereign-guarantee
        # headroom is materially positive for the credit outlook.
        "circular_debt_resolution": (+1, "De-risks bank loan books with "
                                          "sizeable IPP / energy exposure and "
                                          "frees up sovereign-guarantee "
                                          "headroom (small NIM drag on the "
                                          "new financing is more than offset "
                                          "by the credit-quality upgrade)."),
        "circular_debt_worsening":  (-1, "Power-sector loan-book provisioning "
                                          "risk rises and sovereign-guarantee "
                                          "capacity gets eaten."),
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
        "gold_up":   (-1, "Risk-off mood pressures cyclicals like cement."),
        "gold_down": (+1, "Risk-on appetite supports cyclicals."),
        "copper_up": (+1, "Industrial-cycle proxy is strong — supportive "
                          "for construction demand."),
        "copper_down":(-1,"Industrial slowdown reads through to "
                          "construction."),
        # Industry-specific KPIs ------------------------------------
        "tbill_above_policy": (-1, "T-bills above policy rate price-in "
                                    "more hikes — leverage-heavy cement "
                                    "balance sheets bear the cost."),
        "tbill_below_policy": (+1, "T-bills below policy rate signal "
                                    "cuts ahead — cement gets early "
                                    "financial-cost relief."),
        "kibor_up":   (-2, "Higher KIBOR feeds directly into cement "
                            "floating-rate finance costs — immediate "
                            "EPS hit."),
        "kibor_down": (+2, "Lower KIBOR delivers immediate financial-cost "
                            "relief on cement balance sheets."),
        "kibor_shock": (-2, "1-day KIBOR repricing of >=25 bps — cement "
                              "balance sheets carry heavy floating-rate "
                              "debt; an overnight KIBOR jump shows up "
                              "immediately in financial costs and "
                              "typically triggers a 2-3% equity drawdown."),
        "reserves_stress":  (-1, "FX stress raises imported-coal price "
                                  "risk and dents construction confidence."),
        "kse100_up":   (+1, "Broad-market risk-on supports cyclicals."),
        "kse100_down": (-1, "Broad-market risk-off weighs on cyclicals."),
        "cpi_high":    (-1, "Sticky inflation keeps rates restrictive — "
                             "construction demand stays soft."),
        "cpi_easing":  (+2, "Cooling CPI opens the door to cuts — biggest "
                             "single tailwind for leveraged cement."),
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
        "copper_up": (+1, "Industrial-growth read-through is positive "
                          "for energy demand."),
        "copper_down":(-1,"Industrial slowdown signal weighs on energy "
                          "demand."),
        # Industry-specific KPIs ------------------------------------
        "reserves_stress":  (-1, "FX stress raises circular-debt risk "
                                  "(receivables from gas / power chain "
                                  "stay stuck)."),
        "reserves_recovery":(+1, "Reserve rebuild eases circular-debt "
                                  "settlement pressure — cash flow lift."),
        "kse100_up":   (+1, "Risk-on flows support sector multiples."),
        "kse100_down": (-1, "Risk-off flows compress sector multiples."),
        # Circular-debt events ---------------------------------------
        "circular_debt_resolution": (+2, "E&Ps are second-line beneficiaries: "
                                          "receivables from the gas-power "
                                          "chain free up, working-capital "
                                          "compresses, and dividend-paying "
                                          "capacity returns."),
        "circular_debt_worsening":  (-2, "E&P receivables stretch back out — "
                                          "earnings quality and dividend "
                                          "cover both deteriorate."),
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
        # Industry-specific KPIs ------------------------------------
        "kibor_up":   (-1, "Higher KIBOR raises inventory-financing cost."),
        "kibor_down": (+1, "Lower KIBOR cuts inventory-financing cost."),
        "kibor_shock": (-1, "1-day KIBOR repricing of >=25 bps — OMCs "
                              "carry large inventory positions financed "
                              "via short-term KIBOR-linked lines, so a "
                              "jump shows up immediately in working-"
                              "capital cost."),
        "reserves_stress":  (-2, "FX stress jeopardises L/Cs for crude "
                                  "imports and OGRA pricing — historic "
                                  "trigger for OMC profitability shocks."),
        "reserves_recovery":(+1, "L/C confirmation normalises — relief."),
        "kse100_up":   (+1, "Cyclical exposure benefits from broad "
                             "market strength."),
        "kse100_down": (-1, "Cyclical exposure suffers when market sells."),
        # Circular-debt events ---------------------------------------
        "circular_debt_resolution": (+2, "PSO is the largest single creditor "
                                          "to the power sector — any "
                                          "settlement materially improves OMC "
                                          "working capital, financial costs, "
                                          "and the case for special dividends."),
        "circular_debt_worsening":  (-2, "OMC receivables (esp. PSO) stretch "
                                          "and short-term financing costs "
                                          "rise."),
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
        # Industry-specific KPIs ------------------------------------
        "kibor_up":   (-2, "IPPs are heavily leveraged — KIBOR feeds "
                            "straight into financial costs."),
        "kibor_down": (+2, "KIBOR relief is the single biggest near-term "
                            "EPS driver for leveraged IPPs."),
        "kibor_shock": (-2, "1-day KIBOR repricing of >=25 bps — IPPs "
                              "are the most leverage-heavy sector on "
                              "PSX, so an overnight KIBOR jump produces "
                              "an immediate financial-cost hit and a "
                              "sharp equity reaction."),
        "reserves_stress":  (-2, "Reserve stress almost always coincides "
                                  "with worsening circular debt — IPPs "
                                  "see receivables balloon and cash flow "
                                  "deteriorate."),
        "reserves_recovery":(+2, "Reserve rebuilds typically come with "
                                  "circular-debt settlement plans — cash "
                                  "flow normalises and dividends resume."),
        "kse100_down": (-1, "Risk-off flows hit dividend-yield names "
                             "less, but still negative on the margin."),
        # Circular-debt events ---------------------------------------
        "circular_debt_resolution": (+3, "Power sector is the primary "
                                          "beneficiary of any circular-debt "
                                          "settlement — IPP receivables clear, "
                                          "deferred dividends resume, and the "
                                          "equity re-rates as cash flow "
                                          "normalises (the Dec-2025 Rs 1.225 "
                                          "trn clearance is the live "
                                          "playbook)."),
        "circular_debt_worsening":  (-3, "Power sector cash flow is the "
                                          "first to deteriorate when circular "
                                          "debt builds — receivables balloon "
                                          "and IPP dividends are skipped."),
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
        # Industry-specific KPIs ------------------------------------
        "reserves_stress":  (-2, "FX stress squeezes L/Cs for API "
                                  "imports while DRAP MRPs are slow to "
                                  "adjust — gross margin compression "
                                  "risk."),
        "reserves_recovery":(+1, "API import flow normalises — gross "
                                  "margins stabilise."),
        "cpi_high":    (-1, "DRAP MRP increases lag CPI by 2-3 quarters; "
                             "high CPI pinches gross margins until "
                             "revisions catch up."),
        "cpi_easing":  (+1, "Cooling CPI eases the case for further DRAP "
                             "MRP cuts and removes a forward overhang."),
        "kse100_down": (-1, "Defensive but still hit by risk-off flows."),
    },
    "Misc": {  # PABC — aluminium can manufacturer
        "rate_up":   (-1, "Adds to financial costs on working capital."),
        "rate_down": (+1, "Working-capital relief."),
        "oil_up":    (-1, "Energy-intensive aluminium manufacturing pays "
                          "more for power and freight."),
        "oil_down":  (+1, "Energy cost relief."),
        "pkr_weak":  (-2, "Aluminium ingot imports become more expensive."),
        "pkr_strong":(+2, "Imported aluminium ingot becomes cheaper."),
        # Industry-specific KPIs ------------------------------------
        "reserves_stress":  (-2, "Imported aluminium L/C confirmation "
                                  "becomes harder under reserve stress."),
        "reserves_recovery":(+1, "Imports normalise — supply-chain "
                                  "relief."),
        "kibor_up":   (-1, "Higher KIBOR raises working-capital costs."),
        "kibor_down": (+1, "KIBOR relief eases working-capital costs."),
        "kibor_shock": (-1, "1-day KIBOR repricing of >=25 bps — "
                              "working-capital cost spikes immediately."),
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
        # Industry-specific KPIs ------------------------------------
        "kibor_up":   (-2, "Highly leveraged chem balance sheets — KIBOR "
                            "feeds straight into financial cost."),
        "kibor_down": (+2, "Direct EPS lift on KIBOR relief."),
        "kibor_shock": (-2, "1-day KIBOR repricing of >=25 bps — "
                              "leveraged chem balance sheets take an "
                              "immediate financial-cost hit."),
        "reserves_stress":  (-2, "Imported feedstock L/C confirmation "
                                  "tightens and price volatility rises."),
        "reserves_recovery":(+1, "Imports normalise."),
        "cpi_easing":  (+1, "Cooling CPI raises odds of rate cuts — "
                             "direct financial-cost relief."),
        # Circular-debt events ---------------------------------------
        # EPCL and other gas-fed petrochem plants benefit indirectly:
        # SNGPL / SSGC supply reliability improves once their own
        # receivables clear.
        "circular_debt_resolution": (+1, "Indirect lift via SNGPL / SSGC "
                                          "supply reliability once their "
                                          "receivables from the power chain "
                                          "are cleared."),
        "circular_debt_worsening":  (-1, "Gas-supply reliability for "
                                          "petrochem plants deteriorates as "
                                          "the chain's receivables stretch."),
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
    """Persist today's rate observation and return the most recent
    rate seen on a *previous* calendar date (or None if there is no
    earlier observation).

    Persistence rules:
      * One row per calendar date — re-running the same day overwrites
        rather than appending. That keeps the history immune from
        synthetic test runs and from the rule-based fallback that may
        call the engine many times per day.
      * Only the rate from a strictly earlier date is treated as the
        "previous" rate. Multiple values written for *today* never
        produce a phantom rate change.
      * The history file is capped at 50 distinct dates (>2 years of
        MPC decisions, plenty for our purposes).
    """
    if rate_pct is None:
        return None
    hist = _load_rate_history()
    today = datetime.now(timezone.utc).date().isoformat()

    by_date: dict[str, float] = {}
    for row in hist:
        d = row.get("date")
        r = row.get("rate_pct")
        if isinstance(d, str) and isinstance(r, (int, float)):
            by_date[d] = float(r)
    by_date[today] = float(rate_pct)

    # Sort by date and keep last 50 entries
    items = sorted(by_date.items())[-50:]
    rebuilt = [{"date": d, "rate_pct": r} for d, r in items]
    _save_rate_history(rebuilt)

    # Most recent rate observed on a *strictly earlier* date.
    earlier = [r for d, r in items if d < today]
    if not earlier:
        return None
    return earlier[-1]


def _load_circular_debt_events() -> list[dict]:
    """Read the curated circular-debt event log.

    Returns a list of dicts of the form::

        {
          "date": "2025-12-15",
          "type": "resolution" | "worsening",
          "amount_pkr_bn": 1225,             # optional
          "decay_days": 60,                  # how long the driver fires
          "description": "Rs 1.225 trn ($4.29 bn) clearance backed by 18 banks",
          "sectors_override": null           # optional: restrict to a subset
        }

    Why a curated file instead of derived from price data:

    Circular-debt resolution is an **announcement event** — there is no
    market-data series we can z-score. The Dec-2025 Rs 1.225 trillion
    clearance, the 2024 partial settlements, and the Sep-2026 follow-up
    discussions are each a discrete news shock that re-rates the entire
    Power / E&P / OMC complex within hours. We surface them through
    this file so the analyst (or, in future, a news-classifier
    workflow) can stamp the event once and the macro engine fires the
    driver for ``decay_days`` afterward.

    The file is allowed to be missing or empty — the driver simply
    stays silent. Schema validation is lenient on purpose.
    """
    if not CIRCULAR_DEBT_PATH.exists():
        return []
    try:
        raw = json.loads(CIRCULAR_DEBT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = raw.get("events") if isinstance(raw, dict) else raw
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict) and e.get("date")
            and e.get("type") in ("resolution", "worsening")]


def _active_circular_debt_event(today: Optional[datetime] = None) -> Optional[dict]:
    """Return the most recent circular-debt event still inside its
    ``decay_days`` window, or None.

    If multiple events overlap (e.g. a partial worsening followed by a
    resolution two weeks later), the most recent one wins — that
    matches how the market actually reads the tape.
    """
    events = _load_circular_debt_events()
    if not events:
        return None
    now = (today or datetime.now(timezone.utc)).date()
    candidates: list[tuple[int, dict]] = []
    for e in events:
        try:
            d = datetime.fromisoformat(str(e["date"])[:10]).date()
        except Exception:
            continue
        days_since = (now - d).days
        if days_since < 0:
            continue                          # future-dated event, ignore
        decay = int(e.get("decay_days", 60) or 60)
        if days_since > decay:
            continue                          # event has fully decayed
        candidates.append((days_since, e))
    if not candidates:
        return None
    # Most recent wins (smallest days_since).
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1] | {"days_since_event": candidates[0][0]}


def _load_kpi_snapshot() -> dict:
    """Read the persisted industry-KPI parquets and return a snapshot.

    Returned shape (all keys optional)::

        {
          "tbill_3m_pct":          float,
          "tbill_3m_change_5d":    float,    # absolute %-points
          "kibor_3m_pct":          float,
          "kibor_3m_change_5d":    float,
          "reserves_total_usd_mn": float,
          "reserves_change_30d":   float,    # absolute USD mn
          "reserves_sbp_usd_mn":   float,
          "kse100_close":          float,
          "kse100_ret_5d":         float,    # fractional return
          "kse100_ret_21d":        float,
          "cpi_yoy_pct":           float,
          "cpi_period":            str,      # e.g. "March"
        }

    The macro engine treats every field as optional — missing data
    silently skips the matching driver.
    """
    out: dict = {}
    try:
        import pandas as pd
    except Exception:
        return out

    if SBP_RATES_PATH.exists():
        try:
            df = pd.read_parquet(SBP_RATES_PATH).sort_values("date")
            if not df.empty:
                last = df.iloc[-1]
                out["tbill_3m_pct"] = (float(last.get("tbill_3m_pct"))
                                        if last.get("tbill_3m_pct") is not None
                                        else None)
                out["kibor_3m_pct"] = (float(last.get("kibor_3m_pct"))
                                        if last.get("kibor_3m_pct") is not None
                                        else None)
                out["reserves_total_usd_mn"] = (
                    float(last.get("reserves_total_usd_mn"))
                    if last.get("reserves_total_usd_mn") is not None
                    else None)
                out["reserves_sbp_usd_mn"] = (
                    float(last.get("reserves_sbp_usd_mn"))
                    if last.get("reserves_sbp_usd_mn") is not None
                    else None)
                # Compute 1-day changes (absolute %-points). The 1d
                # delta is what catches MPC-day repricing — the +50 bps
                # KIBOR 3M jump on April 28 was invisible to the 5d
                # filter because the prior 4 days were flat.
                if len(df) >= 2:
                    prev = df.iloc[-2]
                    if (last.get("tbill_3m_pct") is not None
                            and prev.get("tbill_3m_pct") is not None):
                        out["tbill_3m_change_1d"] = (
                            float(last["tbill_3m_pct"])
                            - float(prev["tbill_3m_pct"]))
                    if (last.get("kibor_3m_pct") is not None
                            and prev.get("kibor_3m_pct") is not None):
                        out["kibor_3m_change_1d"] = (
                            float(last["kibor_3m_pct"])
                            - float(prev["kibor_3m_pct"]))
                    if (last.get("kibor_12m_pct") is not None
                            and prev.get("kibor_12m_pct") is not None):
                        out["kibor_12m_change_1d"] = (
                            float(last["kibor_12m_pct"])
                            - float(prev["kibor_12m_pct"]))
                # Compute 5-day changes (absolute %-points / USD mn).
                if len(df) >= 6:
                    five = df.iloc[-6]
                    if (last.get("tbill_3m_pct") is not None
                            and five.get("tbill_3m_pct") is not None):
                        out["tbill_3m_change_5d"] = (
                            float(last["tbill_3m_pct"])
                            - float(five["tbill_3m_pct"]))
                    if (last.get("kibor_3m_pct") is not None
                            and five.get("kibor_3m_pct") is not None):
                        out["kibor_3m_change_5d"] = (
                            float(last["kibor_3m_pct"])
                            - float(five["kibor_3m_pct"]))
                if len(df) >= 22:
                    thirty = df.iloc[-22]
                    if (last.get("reserves_total_usd_mn") is not None
                            and thirty.get("reserves_total_usd_mn") is not None):
                        out["reserves_change_30d"] = (
                            float(last["reserves_total_usd_mn"])
                            - float(thirty["reserves_total_usd_mn"]))
        except Exception:
            pass

    if KSE100_PATH.exists():
        try:
            df = pd.read_parquet(KSE100_PATH).sort_values("date")
            if not df.empty:
                last = df.iloc[-1]
                out["kse100_close"] = float(last.get("kse100_close"))
                if len(df) >= 6:
                    five = df.iloc[-6]
                    if five.get("kse100_close"):
                        out["kse100_ret_5d"] = (
                            float(last["kse100_close"])
                            / float(five["kse100_close"]) - 1.0)
                if len(df) >= 22:
                    twenty_one = df.iloc[-22]
                    if twenty_one.get("kse100_close"):
                        out["kse100_ret_21d"] = (
                            float(last["kse100_close"])
                            / float(twenty_one["kse100_close"]) - 1.0)
        except Exception:
            pass

    if CPI_PATH.exists():
        try:
            df = pd.read_parquet(CPI_PATH).sort_values("date")
            if not df.empty:
                last = df.iloc[-1]
                out["cpi_yoy_pct"] = float(last.get("cpi_yoy_pct"))
                out["cpi_period"] = str(last.get("period") or "")
                # CPI direction: compare current value to the value
                # recorded for the previous distinct period text.
                prev_period = df[
                    (df["period"].fillna("") != last.get("period", ""))
                ]
                if not prev_period.empty:
                    prev_last = prev_period.iloc[-1]
                    if prev_last.get("cpi_yoy_pct") is not None:
                        out["cpi_yoy_change_pp"] = (
                            float(last["cpi_yoy_pct"])
                            - float(prev_last["cpi_yoy_pct"]))
        except Exception:
            pass

    return out


def detect_drivers(
    macro: dict | None,
    rate: dict | None,
    kpis: dict | None = None,
) -> list[Driver]:
    """Inspect today's macro snapshot and return the meaningful drivers.

    Thresholds are deliberately conservative — a 1% oil tick is noise,
    a 5% move in 5d or a 10% move in 21d is news.

    ``kpis`` is the industry-KPI snapshot from :func:`_load_kpi_snapshot`
    (T-bill, KIBOR, reserves, KSE-100, CPI). When omitted the engine
    will load it from disk; pass ``{}`` to suppress the new drivers
    explicitly.
    """
    drivers: list[Driver] = []
    indicators = (macro or {}).get("indicators") or {}
    if kpis is None:
        kpis = _load_kpi_snapshot()

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
    brent_stretched, brent_z = _is_stretched("brent", r5)
    stretch_note = (f" [stretched, z={brent_z:+.1f}]"
                    if brent_stretched and brent_z is not None else "")
    if r21 >= 0.10 or r5 >= 0.07:
        mag = ("STRONG" if r21 >= 0.15 else "MODERATE")
        if brent_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Brent crude",
            tag="oil_up",
            move=(f"{r21*100:+.1f}% in 21d" if abs(r21) >= 0.10
                  else f"{r5*100:+.1f}% in 5d") + stretch_note,
            magnitude=mag,
            context=(f"Brent at {brent.get('value', '?')} USD/bbl."
                     + (f" The 5d move sits {brent_z:+.1f}σ from the "
                         "1y mean — statistically extreme, so the rally "
                         "is unlikely to extend in a straight line."
                         if brent_stretched and brent_z is not None
                         else "")),
        ))
    elif r21 <= -0.10 or r5 <= -0.07:
        mag = ("STRONG" if r21 <= -0.15 else "MODERATE")
        if brent_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Brent crude",
            tag="oil_down",
            move=(f"{r21*100:+.1f}% in 21d" if abs(r21) >= 0.10
                  else f"{r5*100:+.1f}% in 5d") + stretch_note,
            magnitude=mag,
            context=(f"Brent at {brent.get('value', '?')} USD/bbl."
                     + (f" The 5d move sits {brent_z:+.1f}σ from the "
                         "1y mean — bounce risk is elevated."
                         if brent_stretched and brent_z is not None
                         else "")),
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

    # ---- Gold (risk-off proxy)
    gold = indicators.get("gold") or {}
    g5  = gold.get("ret_5d") or 0
    g21 = gold.get("ret_21d") or 0
    gold_stretched, gold_z = _is_stretched("gold", g5)
    g_note = (f" [stretched, z={gold_z:+.1f}]"
              if gold_stretched and gold_z is not None else "")
    if g21 >= 0.07:
        mag = ("STRONG" if g21 >= 0.12 else "MODERATE")
        if gold_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Gold",
            tag="gold_up",
            move=f"{g21*100:+.1f}% in 21d" + g_note,
            magnitude=mag,
            context=f"Gold at {gold.get('value', '?')} USD/oz — risk-off "
                     "tone reduces appetite for EM equities.",
        ))
    elif g21 <= -0.05:
        mag = ("STRONG" if g21 <= -0.10 else "MODERATE")
        if gold_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Gold",
            tag="gold_down",
            move=f"{g21*100:+.1f}% in 21d" + g_note,
            magnitude=mag,
            context=f"Gold at {gold.get('value', '?')} USD/oz — risk-on "
                     "tone supports EM equity inflows.",
        ))

    # ---- Copper (industrial growth proxy)
    cop = indicators.get("copper") or {}
    c5  = cop.get("ret_5d") or 0
    c21 = cop.get("ret_21d") or 0
    cop_stretched, cop_z = _is_stretched("copper", c5)
    c_note = (f" [stretched, z={cop_z:+.1f}]"
              if cop_stretched and cop_z is not None else "")
    if c21 >= 0.06:
        mag = ("STRONG" if c21 >= 0.10 else "MODERATE")
        if cop_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Copper",
            tag="copper_up",
            move=f"{c21*100:+.1f}% in 21d" + c_note,
            magnitude=mag,
            context="Strong copper signals global industrial growth — "
                     "supportive of EM cyclicals.",
        ))
    elif c21 <= -0.06:
        mag = ("STRONG" if c21 <= -0.10 else "MODERATE")
        if cop_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Copper",
            tag="copper_down",
            move=f"{c21*100:+.1f}% in 21d" + c_note,
            magnitude=mag,
            context="Copper weakness signals global slowdown — bearish "
                     "for EM cyclicals.",
        ))

    # ---- Cotton (textile sector input cost; future textile tickers)
    ctn = indicators.get("cotton") or {}
    t5  = ctn.get("ret_5d") or 0
    t21 = ctn.get("ret_21d") or 0
    cot_stretched, cot_z = _is_stretched("cotton", t5)
    t_note = (f" [stretched, z={cot_z:+.1f}]"
              if cot_stretched and cot_z is not None else "")
    if t21 >= 0.08:
        mag = ("STRONG" if t21 >= 0.15 else "MODERATE")
        if cot_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Cotton",
            tag="cotton_up",
            move=f"{t21*100:+.1f}% in 21d" + t_note,
            magnitude=mag,
            context="Cotton strength raises raw-material cost for "
                     "Pakistan textile exporters.",
        ))
    elif t21 <= -0.08:
        mag = ("STRONG" if t21 <= -0.15 else "MODERATE")
        if cot_stretched:
            mag = _downshift_magnitude(mag)
        drivers.append(Driver(
            name="Cotton",
            tag="cotton_down",
            move=f"{t21*100:+.1f}% in 21d" + t_note,
            magnitude=mag,
            context="Cotton easing supports textile gross margins.",
        ))

    # ---- T-bill 3M relative to policy rate (banking NIM signal)
    tbill = kpis.get("tbill_3m_pct") if kpis else None
    if tbill is not None and rate_pct is not None:
        gap = tbill - rate_pct
        if gap >= 0.30:
            drivers.append(Driver(
                name="T-bill 3M curve",
                tag="tbill_above_policy",
                move=f"3M cut-off {tbill:.2f}% vs policy {rate_pct:.2f}% "
                     f"(+{gap*100:.0f} bps)",
                magnitude=("STRONG" if gap >= 0.75 else "MODERATE"),
                context="Money market is pricing-in further hikes; banks "
                         "lock in higher yields on the new investment book.",
            ))
        elif gap <= -0.30:
            drivers.append(Driver(
                name="T-bill 3M curve",
                tag="tbill_below_policy",
                move=f"3M cut-off {tbill:.2f}% vs policy {rate_pct:.2f}% "
                     f"({gap*100:.0f} bps)",
                magnitude=("STRONG" if gap <= -0.75 else "MODERATE"),
                context="Money market is pricing-in cuts; banks see "
                         "investment yields compress over coming weeks.",
            ))

    # ---- T-bill 5d trend (banks' weekly funding-yield read)
    tb_chg5 = kpis.get("tbill_3m_change_5d") if kpis else None
    if tb_chg5 is not None:
        if tb_chg5 >= 0.30:
            drivers.append(Driver(
                name="T-bill 3M (5-day move)",
                tag="tbill_up",
                move=f"+{tb_chg5*100:.0f} bps in 5d to {tbill:.2f}%",
                magnitude=("STRONG" if tb_chg5 >= 0.60 else "MODERATE"),
                context="Treasury yields rising — bank investment book "
                         "reprices higher.",
            ))
        elif tb_chg5 <= -0.30:
            drivers.append(Driver(
                name="T-bill 3M (5-day move)",
                tag="tbill_down",
                move=f"{tb_chg5*100:.0f} bps in 5d to {tbill:.2f}%",
                magnitude=("STRONG" if tb_chg5 <= -0.60 else "MODERATE"),
                context="Treasury yields falling — bank investment book "
                         "reprices lower.",
            ))

    # ---- KIBOR 3M (floating-rate loan & financing cost benchmark)
    kibor = kpis.get("kibor_3m_pct") if kpis else None
    kb_chg5 = kpis.get("kibor_3m_change_5d") if kpis else None
    if kb_chg5 is not None and kibor is not None:
        if kb_chg5 >= 0.30:
            drivers.append(Driver(
                name="KIBOR 3M (5-day move)",
                tag="kibor_up",
                move=f"+{kb_chg5*100:.0f} bps in 5d to {kibor:.2f}%",
                magnitude=("STRONG" if kb_chg5 >= 0.60 else "MODERATE"),
                context="Inter-bank funding cost rising — feeds into "
                         "leveraged sector EPS and bank loan yields.",
            ))
        elif kb_chg5 <= -0.30:
            drivers.append(Driver(
                name="KIBOR 3M (5-day move)",
                tag="kibor_down",
                move=f"{kb_chg5*100:.0f} bps in 5d to {kibor:.2f}%",
                magnitude=("STRONG" if kb_chg5 <= -0.60 else "MODERATE"),
                context="Inter-bank funding cost falling — relief on "
                         "leveraged-sector financial costs.",
            ))

    # ---- KIBOR 1-day shock (MPC-day repricing)
    # The plan-level signal that caught yesterday's MPC fallout (KIBOR
    # 3M went 11.165 -> 11.665 overnight, a +50 bps spike).
    # The 5-day filter above missed it because the prior 4 days were
    # flat. The 1-day filter is what prices in MPC decisions live.
    kb_chg1 = kpis.get("kibor_3m_change_1d") if kpis else None
    kb_12_chg1 = kpis.get("kibor_12m_change_1d") if kpis else None
    largest_1d = max(
        [abs(x) for x in (kb_chg1, kb_12_chg1) if x is not None],
        default=None,
    )
    if (kb_chg1 is not None and abs(kb_chg1) >= 0.25) or (
        kb_12_chg1 is not None and abs(kb_12_chg1) >= 0.25
    ):
        # Pick the bigger of 3M / 12M for headline number; sign comes
        # from the 3M leg (the key bank-loan benchmark).
        sign = 1 if (kb_chg1 or kb_12_chg1 or 0) >= 0 else -1
        bps = round((largest_1d or 0) * 100)
        drivers.append(Driver(
            name="KIBOR 1-day shock",
            tag="kibor_shock",
            move=(f"{'+' if sign > 0 else '-'}{bps} bps overnight "
                  f"(3M {kb_chg1*100:+.0f} bps"
                  + (f", 12M {kb_12_chg1*100:+.0f} bps"
                     if kb_12_chg1 is not None else "")
                  + ")"),
            magnitude=("STRONG" if (largest_1d or 0) >= 0.50
                        else "MODERATE"),
            context="An overnight KIBOR repricing of this size almost "
                     "always trails an MPC decision or a sudden T-bill "
                     "auction surprise. Cement, IPPs and other "
                     "leverage-heavy sectors feel it the same day.",
        ))

    # ---- FX reserves regime (BoP stress / recovery)
    rsv = kpis.get("reserves_sbp_usd_mn") if kpis else None
    rsv_chg = kpis.get("reserves_change_30d") if kpis else None
    if rsv is not None:
        if rsv < 8000:
            drivers.append(Driver(
                name="FX reserves",
                tag="reserves_stress",
                move=f"SBP USD {rsv/1000:.1f} bn — sub-$8 bn",
                magnitude="STRONG",
                context="Reserve adequacy below 1.5 months of imports — "
                         "elevated IMF / currency risk.",
            ))
        elif rsv < 10000:
            drivers.append(Driver(
                name="FX reserves",
                tag="reserves_stress",
                move=f"SBP USD {rsv/1000:.1f} bn",
                magnitude="MODERATE",
                context="Reserve buffer thin; markets watch for BoP "
                         "stress and IMF tranche timing.",
            ))
        elif rsv >= 14000 and (rsv_chg or 0) >= 1500:
            drivers.append(Driver(
                name="FX reserves",
                tag="reserves_recovery",
                move=(f"SBP USD {rsv/1000:.1f} bn "
                      f"(+{rsv_chg/1000:.1f} bn in 30d)"
                      if rsv_chg else f"SBP USD {rsv/1000:.1f} bn"),
                magnitude="MODERATE",
                context="Reserve rebuild cuts BoP risk and supports "
                         "broad-market sentiment.",
            ))
        elif rsv >= 14000:
            drivers.append(Driver(
                name="FX reserves",
                tag="reserves_recovery",
                move=f"SBP USD {rsv/1000:.1f} bn",
                magnitude="MILD",
                context="Reserve buffer comfortable — BoP risk muted.",
            ))

    # ---- KSE-100 momentum (broad-market regime)
    kr5 = (kpis.get("kse100_ret_5d") if kpis else None) or 0
    kr21 = (kpis.get("kse100_ret_21d") if kpis else None) or 0
    if kr21 >= 0.05 or kr5 >= 0.04:
        drivers.append(Driver(
            name="KSE-100 momentum",
            tag="kse100_up",
            move=(f"{kr21*100:+.1f}% in 21d" if abs(kr21) >= 0.05
                  else f"{kr5*100:+.1f}% in 5d"),
            magnitude=("STRONG" if kr21 >= 0.10 else "MODERATE"),
            context=f"KSE-100 at "
                     f"{(kpis or {}).get('kse100_close', '?'):.0f} — "
                     "broad market in risk-on mode.",
        ))
    elif kr21 <= -0.05 or kr5 <= -0.04:
        drivers.append(Driver(
            name="KSE-100 momentum",
            tag="kse100_down",
            move=(f"{kr21*100:+.1f}% in 21d" if abs(kr21) >= 0.05
                  else f"{kr5*100:+.1f}% in 5d"),
            magnitude=("STRONG" if kr21 <= -0.10 else "MODERATE"),
            context=f"KSE-100 at "
                     f"{(kpis or {}).get('kse100_close', '?'):.0f} — "
                     "broad market in risk-off mode.",
        ))

    # ---- CPI regime (inflation level + direction)
    cpi = kpis.get("cpi_yoy_pct") if kpis else None
    cpi_chg = kpis.get("cpi_yoy_change_pp") if kpis else None
    if cpi is not None:
        if cpi >= 12.0:
            drivers.append(Driver(
                name="CPI YoY",
                tag="cpi_high",
                move=f"{cpi:.1f}% YoY (sticky)",
                magnitude=("STRONG" if cpi >= 18.0 else "MODERATE"),
                context="Inflation well above SBP comfort band — keeps "
                         "rates restrictive.",
            ))
        elif cpi <= 8.0 and (cpi_chg is None or cpi_chg <= 0):
            drivers.append(Driver(
                name="CPI YoY",
                tag="cpi_easing",
                move=(f"{cpi:.1f}% YoY ({cpi_chg:+.1f}pp vs prior)"
                      if cpi_chg is not None else f"{cpi:.1f}% YoY"),
                magnitude=("STRONG" if cpi <= 5.0 else "MODERATE"),
                context="Inflation cooling — opens room for SBP rate "
                         "cuts that benefit leveraged sectors.",
            ))

    # ---- Circular-debt event (Power / E&P / OMC / Banking complex)
    # Read from a curated event log (`data/macro/circular_debt_events.json`).
    # The 2025-12-15 Rs 1.225 trillion clearance is the canonical recent
    # event the file ships with — see _load_circular_debt_events for the
    # full schema.
    cd = _active_circular_debt_event()
    if cd is not None:
        days_since = int(cd.get("days_since_event", 0))
        decay = int(cd.get("decay_days", 60) or 60)
        amt = cd.get("amount_pkr_bn")
        # Magnitude decays linearly: full strength for the first 14
        # days, then degrades to MILD at the back of the window. We
        # don't downgrade below MILD because event-driven re-ratings on
        # PSX often take 4-8 weeks to fully play out (the Dec-2025
        # clearance is the live example).
        if days_since <= 14:
            mag = "STRONG"
        elif days_since <= max(14, decay // 2):
            mag = "MODERATE"
        else:
            mag = "MILD"

        if cd["type"] == "resolution":
            tag = "circular_debt_resolution"
            human = "Circular-debt resolution"
        else:
            tag = "circular_debt_worsening"
            human = "Circular-debt worsening"

        amt_str = f"PKR {amt:,.0f} bn — " if isinstance(amt, (int, float)) else ""
        move = (f"{amt_str}event +{days_since}d "
                f"(decay window {decay}d)")
        ctx = str(cd.get("description") or "")
        drivers.append(Driver(
            name=human,
            tag=tag,
            move=move,
            magnitude=mag,
            context=ctx,
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

    kpis = _load_kpi_snapshot()
    drivers = detect_drivers(macro, rate, kpis=kpis)
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

    # Pre-MPC alert: if the SBP MPC meets in the next 3 calendar days
    # we soft-cap conviction on rate-sensitive sectors. The cap is a
    # signal the predictions pipeline + UI consume; we do NOT mutate
    # sector scores here, because we want the macro reading itself to
    # remain a clean reflection of *current* data.
    try:
        from config.sbp_mpc_calendar import mpc_alert_state
        mpc = mpc_alert_state()
    except Exception:
        mpc = {"in_pre_window": False, "in_post_window": False,
               "label": "", "rate_sensitive_sectors": []}

    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drivers":   [asdict(d) for d in drivers],
        "by_sector": {s: asdict(v) for s, v in sectors.items()},
        "by_symbol": by_symbol,
        "kpis":      kpis,
        "mpc_alert": mpc,
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
