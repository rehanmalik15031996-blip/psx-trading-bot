"""PSX shorting venue + eligibility reference.

PSX retail short selling is restricted. There are three legitimate
mechanisms for a Pakistani retail account to take a short position:

  1. **Single Stock Futures (SSF / Deliverable Futures)** — the most
     common path. PSX publishes an eligibility list each month
     (~80 names). The contract carries delivery / cash-settlement at
     month end and the typical margin is 25-40 percent of notional.

  2. **Securities Lending & Borrowing (SLB)** — through NCCPL. A much
     smaller set of names is regularly available to borrow because
     supply depends on lenders (institutions). Typical borrow cost
     is 1-3 percent annualised but can spike on hard-to-borrow days.

  3. **Margin Trading System (MTS)** — used for long leverage, NOT
     for shorts. Listed here only so analysts don't confuse it with
     the above.

Below is a hand-maintained, conservative list of PSX-30 names that
are *usually* eligible for at least one of the first two mechanisms.
This is a guide only — the user MUST verify with their broker before
acting. Eligibility changes monthly and a name being on this list
does not guarantee borrow availability or a tradeable futures
contract today.

To keep the bot honest, the Short Ideas tab and chatbot ALWAYS print
the disclaimer at the top of any short recommendation.
"""
from __future__ import annotations


# Conservatively likely-eligible names on the PSX-30 / KSE-100 over
# the last 12 months. Membership is a useful prior, not a contract.
LIKELY_ELIGIBLE: set[str] = {
    "OGDC", "PPL", "POL", "MARI", "PSO", "APL", "ATRL",
    "HBL", "MCB", "UBL", "BAFL", "MEBL", "BAHL", "ABL",
    "FFC", "ENGRO", "EFERT", "FFBL", "DAWH", "EPCL", "LOTCHEM",
    "LUCK", "DGKC", "MLCF", "PIOC", "ACPL", "FCCL",
    "SEARL", "GLAXO", "ABOT",
    "SYS", "NETSOL", "TRG",
    "INDU", "PSMC", "HCAR",
    "NPL", "HUBC", "KAPCO", "KEL",
    "PTC", "PTCL",
}


# Names where shorting carries elevated practical risk for a retail
# account because of (a) thin float, (b) intermittent borrow, or
# (c) historical squeeze episodes. These never get a HIGH-conviction
# short tag from the bot even if signals are extreme.
ELEVATED_SQUEEZE_RISK: set[str] = {
    "TRG", "WTL", "PIBTL", "SILK",
}


def short_eligibility(symbol: str) -> dict:
    """Return a per-symbol short-eligibility hint.

    Output is intentionally conservative — the only guaranteed
    statement is the disclaimer; everything else is best-effort.
    """
    sym = (symbol or "").upper()
    likely = sym in LIKELY_ELIGIBLE
    squeeze = sym in ELEVATED_SQUEEZE_RISK
    notes: list[str] = []
    if likely:
        notes.append("Usually eligible for SSF or SLB on PSX-30. "
                     "Verify with broker.")
    else:
        notes.append("NOT on the bot's likely-eligible list. "
                     "Borrow may be unavailable or expensive — "
                     "confirm with broker before sizing.")
    if squeeze:
        notes.append("Historical squeeze risk: thin float / "
                     "intermittent borrow. Bot will cap conviction "
                     "to MEDIUM at best on this name.")
    return {
        "symbol":            sym,
        "likely_eligible":   likely,
        "squeeze_risk":      squeeze,
        "notes":             notes,
        "disclaimer": (
            "Pakistan retail shorting is only available via Single "
            "Stock Futures or Securities Lending & Borrowing through "
            "NCCPL. Eligibility lists change monthly. Verify both "
            "venue and margin requirements with your broker before "
            "acting on any short recommendation produced by the bot."
        ),
    }
