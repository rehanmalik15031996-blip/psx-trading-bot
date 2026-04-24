"""PSX sector-code → sector-name mapping.

Source: https://www.invest92.com/psx/sectors (mirrors PSX official codes).
The 4-digit sector codes are published by PSX for every listed scrip.

Keeping this as a pure lookup table avoids a dependency on an extra HTTP
call every time we want to resolve a sector name.
"""

from __future__ import annotations

PSX_SECTOR_CODES: dict[str, str] = {
    "0801": "Automobile Assembler",
    "0802": "Automobile Parts & Accessories",
    "0803": "Cable & Electrical Goods",
    "0804": "Cement",
    "0805": "Chemical",
    "0806": "Close-End Mutual Fund",
    "0807": "Commercial Banks",
    "0808": "Engineering",
    "0809": "Fertilizer",
    "0810": "Food & Personal Care Products",
    "0811": "Glass & Ceramics",
    "0812": "Insurance",
    "0813": "Investment Banks / Investment Companies / Securities Cos.",
    "0814": "Jute",
    "0815": "Leasing Companies",
    "0816": "Leather & Tanneries",
    "0817": "Miscellaneous (legacy)",
    "0818": "Miscellaneous",
    "0819": "Modarabas",
    "0820": "Oil & Gas Exploration Companies",
    "0821": "Oil & Gas Marketing Companies",
    "0822": "Paper & Board",
    "0823": "Pharmaceuticals",
    "0824": "Power Generation & Distribution",
    "0825": "Refinery",
    "0826": "Sugar & Allied Industries",
    "0827": "Synthetic & Rayon",
    "0828": "Technology & Communication",
    "0829": "Textile Composite",
    "0830": "Textile Spinning",
    "0831": "Textile Weaving",
    "0832": "Tobacco",
    "0833": "Transport",
    "0834": "Vanaspati & Allied Industries",
    "0835": "Woollen",
    "0836": "Real Estate Investment Trust",
    "0837": "Exchange Traded Funds",
    "0838": "Real Estate / Property (new listings)",
    "40":   "Future Contracts",
}


def sector_name(code: str | None) -> str | None:
    """Resolve a 4-digit PSX sector code to a human-readable name. None-safe."""
    if not code:
        return None
    return PSX_SECTOR_CODES.get(code.strip().zfill(4), f"Unknown ({code})")
