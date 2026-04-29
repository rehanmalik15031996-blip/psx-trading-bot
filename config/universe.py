"""Trading universe: the 35 KSE-100 names the bot trades.

Hand-extended on 2026-04-30 from the prior 16-stock universe to a
35-stock KSE-100-mirroring universe. Sector weights below approximate
the live KSE-100 composition so the long-side picks and the short-side
candidates are evaluated against a representative slice of the market
rather than a narrow blue-chip set.

Composition (35 total):
  - 7 user-required tickers (REQUIRED_TICKERS in config/candidates.py)
  - 9 tickers retained from the previous AUC-ranked selection
  - 19 tickers added 2026-04-30 to span the broader KSE-100 sectors

Sector mix mirrors the KSE-100 (approximate):
  Banking            7   (~22%)
  Oil & Gas E&P      4   (~12%)
  Cement             5   (~14%)
  Power              4   (~11%)
  Fertilizer         3   (~9%)
  OMC / Refining     3   (~9%)
  Chem / Conglo      3   (~9%)
  Technology         2   (~6%)
  Pharma             1   (~3%)
  Consumer           1   (~3%)
  Auto               1   (~3%)
  Misc               1   (~3%)

Edit config/candidates.py (REQUIRED_TICKERS or CANDIDATE_POOL) and
re-run scripts/select_universe.py to rebalance via AUC ranking. The
selector now defaults to n_slots=28 (35 - 7 required); pass --n to
override. Manual edits to this file will be overwritten by the
selector.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    name: str
    sector: str
    notes: str = ""


UNIVERSE: list[UniverseEntry] = [
    # --- Required (locked by user) -----------------------------------
    UniverseEntry("HUBC", "Hub Power", "Power",
                   "[user-required]"),
    UniverseEntry("PABC", "Pakistan Aluminium Beverage Cans", "Misc",
                   "[user-required]"),
    UniverseEntry("MLCF", "Maple Leaf Cement", "Cement",
                   "[user-required]"),
    UniverseEntry("OGDC", "Oil & Gas Development Co.", "Oil & Gas E&P",
                   "[user-required]"),
    UniverseEntry("FABL", "Faysal Bank", "Banking",
                   "[user-required]"),
    UniverseEntry("PPL", "Pakistan Petroleum", "Oil & Gas E&P",
                   "[user-required]"),
    UniverseEntry("NPL", "Nishat Power", "Power",
                   "[user-required]"),

    # --- Retained from prior selection (AUC-ranked) ------------------
    UniverseEntry("POL", "Pakistan Oilfields", "Oil & Gas E&P",
                   "[retained from selector, AUC=0.64]"),
    UniverseEntry("FCCL", "Fauji Cement", "Cement",
                   "[retained from selector, AUC=0.62]"),
    UniverseEntry("APL", "Attock Petroleum", "OMC/Refining",
                   "[retained from selector, AUC=0.61]"),
    UniverseEntry("EPCL", "Engro Polymer", "Conglomerate/Chem",
                   "[retained from selector, AUC=0.61]"),
    UniverseEntry("KOHC", "Kohat Cement", "Cement",
                   "[retained from selector, AUC=0.60]"),
    UniverseEntry("SEARL", "The Searle Company", "Pharma",
                   "[retained from selector, AUC=0.59]"),
    UniverseEntry("MCB", "MCB Bank", "Banking",
                   "[retained from selector, AUC=0.57]"),
    UniverseEntry("MEBL", "Meezan Bank", "Banking",
                   "[retained from selector, AUC=0.55]"),
    UniverseEntry("PSO", "Pakistan State Oil", "OMC/Refining",
                   "[retained from selector, AUC=0.54]"),

    # --- KSE-100 expansion 2026-04-30 (top constituents by sector) ---
    # Banking — biggest index weight, broaden bank coverage
    UniverseEntry("HBL", "Habib Bank", "Banking",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("UBL", "United Bank", "Banking",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("BAHL", "Bank Al Habib", "Banking",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("NBP", "National Bank of Pakistan", "Banking",
                   "[KSE-100 expansion 2026-04-30]"),

    # E&P
    UniverseEntry("MARI", "Mari Petroleum", "Oil & Gas E&P",
                   "[KSE-100 expansion 2026-04-30]"),

    # Cement
    UniverseEntry("LUCK", "Lucky Cement", "Cement",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("DGKC", "D.G. Khan Cement", "Cement",
                   "[KSE-100 expansion 2026-04-30]"),

    # Fertilizer
    UniverseEntry("FFC", "Fauji Fertilizer", "Fertilizer",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("EFERT", "Engro Fertilizers", "Fertilizer",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("FATIMA", "Fatima Fertilizer", "Fertilizer",
                   "[KSE-100 expansion 2026-04-30]"),

    # Power
    UniverseEntry("KAPCO", "Kot Addu Power", "Power",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("KEL", "K-Electric", "Power",
                   "[KSE-100 expansion 2026-04-30]"),

    # Refining
    UniverseEntry("ATRL", "Attock Refinery", "OMC/Refining",
                   "[KSE-100 expansion 2026-04-30]"),

    # Conglomerate / Chem
    UniverseEntry("ENGROH", "Engro Holdings", "Conglomerate/Chem",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("LOTCHEM", "Lotte Chemical Pakistan", "Conglomerate/Chem",
                   "[KSE-100 expansion 2026-04-30]"),

    # Technology
    UniverseEntry("SYS", "Systems Ltd", "Technology",
                   "[KSE-100 expansion 2026-04-30]"),
    UniverseEntry("TRG", "TRG Pakistan", "Technology",
                   "[KSE-100 expansion 2026-04-30]"),

    # Auto
    UniverseEntry("INDU", "Indus Motor", "Autos",
                   "[KSE-100 expansion 2026-04-30]"),

    # Consumer
    UniverseEntry("COLG", "Colgate Palmolive Pakistan", "Consumer",
                   "[KSE-100 expansion 2026-04-30]"),
]


def symbols() -> list[str]:
    return [u.symbol for u in UNIVERSE]


def sector_of(symbol: str) -> str | None:
    for u in UNIVERSE:
        if u.symbol == symbol:
            return u.sector
    return None


def by_sector() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for u in UNIVERSE:
        out.setdefault(u.sector, []).append(u.symbol)
    return out
