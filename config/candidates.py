"""Candidate pool for universe selection.

These are liquid PSX blue-chip names (mostly KSE-30 / KMI-30 constituents)
that the selector considers for the 9 "flex" slots. The 6 user-required
tickers live in REQUIRED_TICKERS.

Having a curated pool means we don't backfill all 500+ PSX names every time.
All candidates have 5+ years of DPS history as of 2026-04-23.
"""

from __future__ import annotations

# User's non-negotiable tickers — always in the universe.
REQUIRED_TICKERS: list[str] = [
    "HUBC",   # Hub Power (Power Generation)
    "PABC",   # Pakistan Aluminium Beverage Cans (Miscellaneous)
    "MLCF",   # Maple Leaf Cement (Cement)
    "OGDC",   # Oil & Gas Development (Oil & Gas E&P)
    "FABL",   # Faysal Bank (Commercial Banks)
    "PPL",    # Pakistan Petroleum (Oil & Gas E&P)
    "NPL",    # Nishat Power (Power Generation — IPP)
]

# Known sector for each candidate — used for diversification constraint.
# This is the PSX canonical sector (from Market Watch sector codes).
CANDIDATE_POOL: list[tuple[str, str]] = [
    # --- Banking ---
    ("MCB",    "Commercial Banks"),
    ("HBL",    "Commercial Banks"),
    ("UBL",    "Commercial Banks"),
    ("MEBL",   "Commercial Banks"),
    ("BAHL",   "Commercial Banks"),
    ("ABL",    "Commercial Banks"),
    ("NBP",    "Commercial Banks"),

    # --- Oil & Gas E&P ---
    ("MARI",   "Oil & Gas Exploration Companies"),
    ("POL",    "Oil & Gas Exploration Companies"),

    # --- OMC / Refining ---
    ("PSO",    "Oil & Gas Marketing Companies"),
    ("APL",    "Oil & Gas Marketing Companies"),
    ("ATRL",   "Refinery"),

    # --- Cement ---
    ("LUCK",   "Cement"),
    ("FCCL",   "Cement"),
    ("DGKC",   "Cement"),
    ("KOHC",   "Cement"),

    # --- Fertilizer ---
    ("FFC",    "Fertilizer"),
    ("EFERT",  "Fertilizer"),

    # --- Power (besides HUBC) ---
    ("KAPCO",  "Power Generation & Distribution"),
    ("KEL",    "Power Generation & Distribution"),

    # --- Conglomerate / Chemical ---
    ("ENGROH", "Cable & Electrical Goods"),    # Engro Holdings
    ("LOTCHEM","Chemical"),
    ("EPCL",   "Chemical"),

    # --- Technology ---
    ("SYS",    "Technology & Communication"),
    ("TRG",    "Technology & Communication"),

    # --- Auto / Food ---
    ("INDU",   "Automobile Assembler"),
    ("SEARL",  "Pharmaceuticals"),
    ("COLG",   "Food & Personal Care"),
]


def all_candidates_including_required() -> list[str]:
    return list(REQUIRED_TICKERS) + [c[0] for c in CANDIDATE_POOL]


def sector_of_candidate(symbol: str) -> str | None:
    for s, sec in CANDIDATE_POOL:
        if s == symbol:
            return sec
    # required tickers — canonical sectors
    required_sectors = {
        "HUBC":  "Power Generation & Distribution",
        "PABC":  "Miscellaneous",
        "MLCF":  "Cement",
        "OGDC":  "Oil & Gas Exploration Companies",
        "FABL":  "Commercial Banks",
        "PPL":   "Oil & Gas Exploration Companies",
        "NPL":   "Power Generation & Distribution",
    }
    return required_sectors.get(symbol)
