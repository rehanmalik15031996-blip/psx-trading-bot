"""Candidate pool for universe selection.

These are liquid KSE-100 constituents that the selector considers for
the "flex" slots. The 7 user-required tickers live in REQUIRED_TICKERS.

Having a curated pool means we don't backfill all 500+ PSX names every
time. All candidates target 5+ years of price history and >= 1bn PKR
market cap to ensure they are tradeable.

Last expanded: 2026-04-30 — universe scaled from 16 → 35 names to
mirror the broader KSE-100 sector composition.
"""

from __future__ import annotations

REQUIRED_TICKERS: list[str] = [
    "HUBC",   # Hub Power (Power Generation)
    "PABC",   # Pakistan Aluminium Beverage Cans (Miscellaneous)
    "MLCF",   # Maple Leaf Cement (Cement)
    "OGDC",   # Oil & Gas Development (Oil & Gas E&P)
    "FABL",   # Faysal Bank (Commercial Banks)
    "PPL",    # Pakistan Petroleum (Oil & Gas E&P)
    "NPL",    # Nishat Power (Power Generation — IPP)
]

CANDIDATE_POOL: list[tuple[str, str]] = [
    # --- Banking (KSE-100 weight ~25-30%) ---
    ("MCB",    "Commercial Banks"),
    ("HBL",    "Commercial Banks"),
    ("UBL",    "Commercial Banks"),
    ("MEBL",   "Commercial Banks"),
    ("BAHL",   "Commercial Banks"),
    ("ABL",    "Commercial Banks"),
    ("NBP",    "Commercial Banks"),
    ("AKBL",   "Commercial Banks"),

    # --- Oil & Gas E&P (top index weight) ---
    ("MARI",   "Oil & Gas Exploration Companies"),
    ("POL",    "Oil & Gas Exploration Companies"),

    # --- OMC / Refining ---
    ("PSO",    "Oil & Gas Marketing Companies"),
    ("APL",    "Oil & Gas Marketing Companies"),
    ("ATRL",   "Refinery"),
    ("NRL",    "Refinery"),

    # --- Cement ---
    ("LUCK",   "Cement"),
    ("FCCL",   "Cement"),
    ("DGKC",   "Cement"),
    ("KOHC",   "Cement"),
    ("BWCL",   "Cement"),
    ("CHCC",   "Cement"),

    # --- Fertilizer ---
    ("FFC",    "Fertilizer"),
    ("EFERT",  "Fertilizer"),
    ("FATIMA", "Fertilizer"),

    # --- Power (besides HUBC and NPL) ---
    ("KAPCO",  "Power Generation & Distribution"),
    ("KEL",    "Power Generation & Distribution"),

    # --- Conglomerate / Chemical ---
    ("ENGROH", "Cable & Electrical Goods"),
    ("LOTCHEM", "Chemical"),
    ("EPCL",   "Chemical"),
    ("ICI",    "Chemical"),

    # --- Technology ---
    ("SYS",    "Technology & Communication"),
    ("TRG",    "Technology & Communication"),
    ("AVN",    "Technology & Communication"),

    # --- Auto ---
    ("INDU",   "Automobile Assembler"),
    ("HCAR",   "Automobile Assembler"),
    ("PSMC",   "Automobile Assembler"),

    # --- Pharma ---
    ("SEARL",  "Pharmaceuticals"),
    ("GLAXO",  "Pharmaceuticals"),
    ("ABOT",   "Pharmaceuticals"),

    # --- Consumer / Food ---
    ("COLG",   "Food & Personal Care"),
    ("NESTLE", "Food & Personal Care"),
    ("UFL",    "Food & Personal Care"),

    # --- Textile ---
    ("NML",    "Textile Composite"),
    ("GATM",   "Textile Composite"),
]


def all_candidates_including_required() -> list[str]:
    return list(REQUIRED_TICKERS) + [c[0] for c in CANDIDATE_POOL]


def sector_of_candidate(symbol: str) -> str | None:
    for s, sec in CANDIDATE_POOL:
        if s == symbol:
            return sec
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
