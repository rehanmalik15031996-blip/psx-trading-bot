"""Download and parse AHL "Mutual Funds Equity Holdings" monthly PDFs.

Source: Arif Habib Limited (AHL) Research, report code REP-300, published
~2 weeks after each month-end at https://arifhabibltd.com/api/research/open .

The PDF URLs use opaque MongoDB-style ObjectIds (e.g.
``178/6996b790...4954b11f.pdf``). The ``178`` is the report-type ID
(Mutual Funds Holdings); the second token is a 24-char hex ObjectId
whose first 4 bytes (8 hex chars) ARE a unix timestamp. That's enough
to reconstruct the publication date from the URL alone, which we use
to label the report.

This module ships with a curated list of known URLs (built from web
search). It re-downloads any that are missing from disk, parses each,
and appends to ``data/flows/mutual_fund_holdings.parquet``.

The PDF schema is consistent month-to-month:

  * Page 1-2:    cover and table of contents.
  * Page 3:      "Funds' Favorites: A Glimpse into Mutual Fund Equity
                  Exposure" -- THE summary table:
                    ``Symbol | No of Funds | Holding as % of FF``
                  This gives us per-stock breadth (n_funds_holding) and
                  aggregate institutional exposure (% of free float).
  * Page 4-onwards: per-fund pages (one per ~15-90 funds across
                  ~14 AMCs). Each page has:
                    ``Fund Name`` (often spans 2 lines)
                    ``Fund Size (Rs. In '000)`` last & current month
                    Asset split (Cash / Equity / TBills / Others %)
                    Two ``Top Holdings`` tables, one for the prior
                    month ("May'25") and one for the report month
                    ("Jun'25"), each with rows:
                       ``Symbol | % of Total Fund Size | No of Shares (000)``

We extract BOTH tables per fund -- the report bundles its own
month-over-month delta inside the PDF, which is exactly what our
30-day signals need. So even with a single PDF on disk, the matcher
already has 1 month of MoM accumulation/distribution data per fund x
stock.

Output parquet schema (long format):

    as_of_month     YYYY-MM-01 (1st of the month the report COVERS,
                    not the publication month)
    report_pub_date YYYY-MM-DD publication date (from ObjectId timestamp)
    fund_name       e.g. "NIT Pakistan Gateway Exchange Traded Fund"
    amc             e.g. "National Investment Trust Limited"
    fund_size_pkr_thousand  numeric
    pct_cash        0-100
    pct_equity      0-100
    pct_tbills      0-100
    pct_others      0-100
    symbol          PSX ticker
    pct_of_fund     0-100, share of fund AUM in this stock
    n_shares_000    thousands of shares held
    section         "current" or "prior" (which of the two MoM tables)
    source_pdf      filename basename
    source_url      the AHL URL

Plus a separate ``data/flows/mf_top_holdings_summary.parquet`` for
the page-3 summary table:

    as_of_month, symbol, n_funds_holding, holding_pct_of_ff,
    holding_pkr_mn (when present in the column), source_pdf

Usage::

    python scripts/ingest_ahl_mf_holdings.py            # download + parse all known URLs
    python scripts/ingest_ahl_mf_holdings.py --no-download   # parse what's on disk only
    python scripts/ingest_ahl_mf_holdings.py --validate      # type-check parquets
    python scripts/ingest_ahl_mf_holdings.py --add URL       # add a URL ad-hoc and ingest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

RAW_DIR = ROOT / "data" / "raw" / "mf_holdings"
OUT_PARQUET = ROOT / "data" / "flows" / "mutual_fund_holdings.parquet"
SUMMARY_PARQUET = ROOT / "data" / "flows" / "mf_top_holdings_summary.parquet"
URL_INDEX = ROOT / "data" / "raw" / "mf_holdings" / "_known_urls.json"

# ---------------------------------------------------------------------------
# Known PDF URLs (built from web research). Each is the AHL-published
# report for a given month. ``report_month`` is the month the report
# COVERS (one before publication date). ObjectId-derived publication
# date is computed at runtime from the hash itself.
# ---------------------------------------------------------------------------
KNOWN_URLS: list[tuple[str, str]] = [
    # (objectid_hash, report_month_iso)
    # NOTE: AHL's path=178 namespace is shared across multiple report
    # types (PSX Performance, MSCI Review, KSE-100 Profitability, MF
    # Holdings, etc.). The parser auto-detects "Mutual Funds Holdings"
    # on page 1-2 and skips others, so it's safe to enumerate generously.
    ("658ea5de844813743dfa8ca6", "2023-11-01"),  # Nov-2023
    ("660a6329d5c8a55d81deb0f0", "2024-03-01"),  # Mar-2024
    ("66fac90d8f84bab98c8f5430", "2024-09-01"),  # Sep-2024 (publication Sep-30-2024)
    ("67651a5decfd7de5dd83f6f4", "2024-12-01"),  # Dec-2024 (publication Dec-20-2024)
    ("67ac706c8ad2dd7a1ec38f18", "2025-01-01"),  # Jan-2025
    ("67c20825881e05effa4cc27c", "2025-02-01"),  # Feb-2025 (publication Feb-26-2025)
    ("67e66c235611029bb9d5f076", "2025-03-01"),  # Mar-2025 (publication Mar-28-2025)
    ("68124e8748ca0138d5b67cbe", "2025-04-01"),  # Apr-2025 (publication Apr-30-2025)
    ("682431c8e996d4d44fa4f86f", "2025-04-01"),  # alt Apr-2025 revision
    ("6839f70e63c89234628b59cc", "2025-05-01"),  # May-2025 (publication May-30-2025)
    ("68789e35a8d6d2adc8edbfb3", "2025-06-01"),  # Jun-2025 (full per-fund detail)
    ("68b1d423d345d03b22dcd924", "2025-08-01"),  # Aug-2025
    ("68d2b8e46cfd11cfccd9632b", "2025-09-01"),  # Sep-2025
    ("69553ca0b34f60500c56d113", "2025-10-01"),  # Oct-2025
    ("6996b7902c2a6a5d4954b11f", "2026-01-01"),  # Jan-2026 (summary w/ MoM column)
    ("6929c23bae4e0b8d028cea63", "2025-11-01"),  # Nov-2025 (web-discovered 2026-05-03)
]


# ---------------------------------------------------------------------------
# Where mis-categorised PDFs (Market Performance / Strategy / Profitability)
# get moved so the MF parser doesn't keep re-trying them. Discovered during
# the 2026-05-02 audit that 14 of 16 PDFs in the path=178 namespace are
# different report types entirely.
# ---------------------------------------------------------------------------
MISCATEGORIZED_DIR = ROOT / "data" / "raw" / "ahl_market_reports"

AHL_BASE = "https://arifhabibltd.com/api/research/open?path=178/{hash}.pdf"

# Set of AMC names to recognise per-page (as they appear in the PDF). New
# AMCs can be added without code change since we also fall back to "any
# all-caps line that ends with 'Limited' or 'Investments'". Order matters
# because the PDF prints AMC banners as section headers; we use them to
# attribute funds to AMCs.
KNOWN_AMCS: list[str] = [
    "National Investment Trust Limited",
    "Al Meezan Investment Management Limited",
    "NBP Fund Management Limited",
    "UBL Fund Managers Limited",
    "Atlas Asset Management Limited",
    "MCB Investment Management Limited",
    "Alfalah Asset Management Limited",
    "HBL Asset Management Limited",
    "JS Investments Limited",
    "ABL Asset Management Company Limited",
    "AKD Investment Management Limited",
    "Lakson Investments Limited",
    "Lucky Investments Limited",
    "Faysal Asset Management Limited",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pub_date_from_hash(hash_str: str) -> datetime | None:
    """ObjectId-style 24-char hex: first 4 bytes (8 hex chars) are unix
    timestamp."""
    try:
        ts = int(hash_str[:8], 16)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _filename_for(hash_str: str, report_month: str) -> str:
    return f"MutualFundsEquityHoldings-{report_month[:7]}_{hash_str[:8]}.pdf"


def _download_one(hash_str: str, report_month: str, force: bool = False) -> Path | None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fname = _filename_for(hash_str, report_month)
    out = RAW_DIR / fname
    if out.exists() and out.stat().st_size > 10_000 and not force:
        return out
    url = AHL_BASE.format(hash=hash_str)
    print(f"  downloading {report_month}  {url}")
    try:
        r = requests.get(url, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out.write_bytes(r.content)
        print(f"    saved {len(r.content):,} bytes -> {out.name}")
        return out
    except Exception as e:
        print(f"    FAILED: {e}")
        return None


def _persist_url_index(seen: list[dict]) -> None:
    URL_INDEX.parent.mkdir(parents=True, exist_ok=True)
    URL_INDEX.write_text(json.dumps(seen, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"^[\d,\.\-]+%?$")
_PCT_RE = re.compile(r"^([\d\.]+)%$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,8}$")  # PSX tickers are 2-9 uppercase chars
_FUND_SIZE_RE = re.compile(r"Fund Size.*?([\d,]+)\s+([\d,]+)", re.I)


def _parse_int(s: str) -> int | None:
    s = (s or "").replace(",", "").strip()
    if not s or s in ("-", "N/A"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_float(s: str) -> float | None:
    s = (s or "").replace(",", "").replace("%", "").strip()
    if not s or s in ("-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _month_token_to_iso(token: str) -> str | None:
    """Convert tokens like 'Jun'25', 'May-25', 'Jun-2025' to 'YYYY-MM-01'."""
    if not token:
        return None
    t = token.strip().replace("'", "-").replace(" ", "-")
    for fmt in ("%b-%y", "%b-%Y", "%B-%y", "%B-%Y"):
        try:
            return datetime.strptime(t, fmt).date().replace(day=1).isoformat()
        except ValueError:
            continue
    return None


_MF_HEADER_RE = re.compile(
    r"(Mutual Funds (Holdings|Equity Holdings)|"
    r"Top Holdings by Mutual Funds|Funds(['\u2019])? Favou?rites)",
    re.I,
)


def _is_mf_report(pdf) -> bool:
    """Inspect first 3 pages -- if no MF banner, this is a different
    AHL report sitting in the same path=178 namespace."""
    for idx in range(min(3, len(pdf.pages))):
        text = pdf.pages[idx].extract_text() or ""
        if _MF_HEADER_RE.search(text):
            return True
    return False


def _move_to_market_reports(p: Path, retries: int = 5) -> None:
    """Relocate a mis-categorised PDF (Market Performance / Strategy
    / Profitability) into ``data/raw/ahl_market_reports/`` so the next
    discovery run doesn't keep re-parsing it. The file is renamed with
    the ``AHLMarketPerformance-`` prefix so the secondary ingester picks
    it up automatically.

    On Windows pdfplumber's lazy memory-mapped IO can keep the file
    handle open briefly after the ``with`` block exits, so we retry the
    move a few times rather than fail loudly."""
    import shutil
    import time
    MISCATEGORIZED_DIR.mkdir(parents=True, exist_ok=True)
    new_name = p.name.replace("MutualFundsEquityHoldings-",
                               "AHLMarketPerformance-")
    dst = MISCATEGORIZED_DIR / new_name
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            if dst.exists():
                p.unlink(missing_ok=True)
            else:
                shutil.move(str(p), str(dst))
            print(f"  moved -> ahl_market_reports/{dst.name}")
            return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    print(f"  WARNING: could not move mis-categorised PDF after "
          f"{retries} attempts: {last_err}")


def _detect_summary_metric_kind(text: str) -> str:
    """Detect whether the page-3 summary uses '% of FF' or
    '% of Equity AUMs' as its denominator. They are NOT comparable."""
    # Order matters: equity AUMs is a more specific match
    low = text.lower()
    if "% of equity aums" in low or "% of equity aum" in low:
        return "equity_aums"
    if "% of ff" in low or "free float" in low:
        return "ff"
    return "unknown"


def _parse_summary_table(text: str, report_month: str,
                          pub_iso: str | None,
                          pdf_name: str) -> list[dict]:
    """Parse the page-3 "Top Holdings by Mutual Funds" table.

    Two layouts seen in the wild (and they use DIFFERENT denominators):

    * Old (pre-Dec-2025): ``Symbol | No of Funds | Holding as % of FF``
        Row example: ``OGDC 62 17.24%``.
        Metric: percent of the stock's free float owned by all funds.
    * New (Dec-2025+):    ``Symbol | No of Funds | <pct curr> | <pct prior> | Change MoM | Funds Holding (PKR mn)``
        Row example: ``OGDC 85 6.9% 7.1% -0.2% 51,874``.
        Metric: percent of total industry equity AUMs in this stock.

    Each row gets ``metric_kind`` so downstream signals only compute
    deltas within the same metric.
    """
    metric_kind = _detect_summary_metric_kind(text)
    rows: list[dict] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        sym = parts[0]
        if not _SYMBOL_RE.match(sym) or sym in ("PKR", "USD", "FF", "MoM",
                                                  "AUMs", "AUM"):
            continue
        n_funds = None
        pcts: list[float] = []
        pkr_mn = None
        for tok in parts[1:]:
            pct_m = _PCT_RE.match(tok)
            if pct_m:
                try:
                    pcts.append(float(pct_m.group(1)))
                    continue
                except ValueError:
                    pass
            # Signed percent like "-0.2%"
            m_signed = re.match(r"^(-?\d+(?:\.\d+)?)%$", tok)
            if m_signed:
                pcts.append(float(m_signed.group(1)))
                continue
            cleaned = tok.replace(",", "")
            if n_funds is None and cleaned.isdigit() and int(cleaned) < 200:
                n_funds = int(cleaned)
                continue
            # PKR mn: large numeric (>= 100 usually thousands or millions)
            try:
                v = float(cleaned)
                if v >= 100 and "," in tok:
                    if pkr_mn is None or v > pkr_mn:
                        pkr_mn = v
            except ValueError:
                pass
        if n_funds is None or not pcts:
            continue
        rows.append({
            "as_of_month": report_month,
            "symbol": sym,
            "n_funds_holding": n_funds,
            "holding_pct": pcts[0],
            "holding_pct_prior_month": pcts[1] if len(pcts) > 1 else None,
            "change_mom_pct_pts": pcts[2] if len(pcts) > 2 else None,
            "holding_pkr_mn": pkr_mn,
            "metric_kind": metric_kind,
            "source_pdf": pdf_name,
            "report_pub_date": pub_iso,
        })
    return rows


def _parse_pdf(pdf_path: Path, report_month: str,
                pub_date: datetime | None) -> tuple[list[dict], list[dict]]:
    """Extract (fund_holdings_rows, summary_rows) from one PDF.

    Returns long-format records ready for the parquet. Returns ``([], [])``
    if the PDF is not a Mutual Funds Holdings report.
    """
    import pdfplumber  # lazy
    fund_rows: list[dict] = []
    summary_rows: list[dict] = []

    pub_iso = pub_date.date().isoformat() if pub_date else None
    with pdfplumber.open(str(pdf_path)) as pdf:
        if not _is_mf_report(pdf):
            print(f"  [skip] not a Mutual Funds Holdings report")
            # Signal to caller that this PDF should be moved out of
            # mf_holdings/. Returning a special marker tuple keeps the
            # pdf handle out of the move call (Windows file-lock).
            return None, None  # type: ignore[return-value]

        # ---- 1) Find and parse the page that holds the summary -------
        # The "Top Holdings by Mutual Funds" table is sometimes on
        # page 3, sometimes page 4 if there's an extra cover page.
        # Page 2 typically has a table-of-contents that ALSO matches
        # ("Funds' Favorites: A Glimpse...") so we walk forward and
        # accept the first page that yields >= 5 parseable holding
        # rows. Empty matches are silently skipped.
        for idx, page in enumerate(pdf.pages[:8]):  # never beyond page 8
            text = page.extract_text() or ""
            if not text.strip():
                continue
            if not (
                "Top Holdings by Mutual Funds" in text
                or "Top 30 Holdings by Mutual Funds" in text
                or "Funds' Favorites" in text
                or "Funds\u2019 Favorites" in text
                or "Top Holdings" in text and idx <= 3
            ):
                continue
            candidate = _parse_summary_table(
                text, report_month, pub_iso, pdf_path.name,
            )
            if len(candidate) >= 5:
                summary_rows.extend(candidate)
                break

        # ---- 2) Per-fund pages (page 4 onwards) ------------------------
        # State machine: track current AMC (heading) + current fund name.
        current_amc = None
        for page_idx in range(3, len(pdf.pages)):
            page = pdf.pages[page_idx]
            text = page.extract_text() or ""
            if not text.strip():
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            # Detect AMC headers: a line that exactly matches a known AMC.
            for ln in lines[:3]:  # AMC heading is always near the top
                for amc in KNOWN_AMCS:
                    if amc.lower() in ln.lower():
                        current_amc = amc
                        break
            # Fund name: the line immediately after the AMC banner
            # (or if AMC is absent, the first non-page-number line).
            # In practice the AHL layout has:
            #   Line[0] = AMC name (sometimes)
            #   Line[1] = Fund name
            #   Line[2] = "<page_number>"  e.g. "5"
            #   Line[3] = "May-25 Jun-25"
            #   ...
            fund_name = None
            for i, ln in enumerate(lines[:6]):
                if any(kw in ln for kw in ("Fund", "Trust", "ETF", "Income",
                                              "Stock", "Equity", "Allocation",
                                              "Pension", "Asset", "Index",
                                              "Multi Asset", "Strategic",
                                              "Capital Preservation")):
                    if not any(amc.lower() in ln.lower() for amc in KNOWN_AMCS):
                        fund_name = ln.strip()
                        break
            if not fund_name:
                continue

            # Fund size + asset split
            fund_size = None
            asset_split = {"cash": None, "equity": None,
                            "tbills": None, "others": None}
            month_tokens: list[str] = []  # the two MoM headers, e.g. ["May-25","Jun-25"]
            for ln in lines:
                m = _FUND_SIZE_RE.search(ln)
                if m:
                    # Take the SECOND number (current month)
                    fund_size = _parse_int(m.group(2))
                low = ln.lower()
                if low.startswith("cash"):
                    pcts = re.findall(r"(\d+)%", ln)
                    if len(pcts) >= 2:
                        asset_split["cash"] = float(pcts[-1])
                elif low.startswith("equity"):
                    pcts = re.findall(r"(\d+)%", ln)
                    if len(pcts) >= 2:
                        asset_split["equity"] = float(pcts[-1])
                elif low.startswith("t bills") or low.startswith("tbills") or low.startswith("t-bills"):
                    pcts = re.findall(r"(\d+)%", ln)
                    if len(pcts) >= 2:
                        asset_split["tbills"] = float(pcts[-1])
                elif low.startswith("others"):
                    pcts = re.findall(r"(\d+)%", ln)
                    if len(pcts) >= 2:
                        asset_split["others"] = float(pcts[-1])
                # Month header line: "May-25 Jun-25" or "May'25 Jun'25"
                if not month_tokens:
                    parts = ln.split()
                    if len(parts) == 2:
                        m_iso = _month_token_to_iso(parts[0])
                        n_iso = _month_token_to_iso(parts[1])
                        if m_iso and n_iso and m_iso < n_iso:
                            month_tokens = [m_iso, n_iso]

            # ---- Top Holdings tables (two side-by-side per fund page) ---
            # Layout: each page has TWO holdings tables printed
            # side-by-side (current month on the LEFT, prior month on
            # the RIGHT). pdfplumber's text extractor joins them into
            # single physical lines like:
            #     "Jun'25 Top Holdings   May'25 Top Holdings"
            #     "PSO 14.1% 32,883       PSO 14.2% 33,819"
            # We split each row into LEFT and RIGHT triples (sym, pct,
            # n_shares) and emit two records (current + prior).
            #
            # Some pages only have ONE table (e.g. very small funds);
            # we detect that from the heading line.
            two_table_layout = False
            if month_tokens:
                # Look for a header line that mentions BOTH months
                for ln in lines:
                    m_iso = None
                    n_iso = None
                    if "Top Holdings" in ln:
                        # split on "Top Holdings"
                        m1 = re.match(
                            r"([A-Z][a-z]+)['\-]?(\d{2,4})\s+Top Holdings\s+"
                            r"([A-Z][a-z]+)['\-]?(\d{2,4})\s+Top Holdings",
                            ln,
                        )
                        if m1:
                            two_table_layout = True
                            break

            for ln in lines:
                # Skip table headers
                if "Symbol" in ln and ("Fund Size" in ln
                                         or "Shares" in ln
                                         or "of Fund" in ln):
                    continue
                if ln.lower().startswith("source"):
                    continue
                if ("Top Holdings" in ln
                        or "Fund Size" in ln
                        or "Total" in ln):
                    continue
                parts = ln.split()
                if len(parts) < 2:
                    continue

                # Try to find symbol + pct + shares triples.
                triples: list[tuple[str, float, int | None]] = []
                i = 0
                while i < len(parts):
                    sym = parts[i]
                    if not _SYMBOL_RE.match(sym) or sym in ("PKR", "USD",
                                                              "FF", "MoM",
                                                              "AUMs", "AUM"):
                        i += 1
                        continue
                    # Look ahead for pct and shares
                    pct = None
                    n_shares = None
                    j = i + 1
                    while j < len(parts) and j < i + 4:
                        tok = parts[j]
                        pct_m = _PCT_RE.match(tok)
                        if pct_m and pct is None:
                            try:
                                pct = float(pct_m.group(1))
                                j += 1
                                continue
                            except ValueError:
                                pass
                        if pct is not None and n_shares is None:
                            v = _parse_int(tok)
                            if v is not None and v > 0:
                                n_shares = v
                                j += 1
                                continue
                        break
                    if pct is not None:
                        triples.append((sym, pct, n_shares))
                        i = j
                    else:
                        i += 1

                if not triples:
                    continue

                # If two-table layout AND we got 2 triples on this line,
                # the FIRST is the current month (LEFT col), SECOND is
                # prior (RIGHT col). Otherwise treat as current only.
                if two_table_layout and len(triples) == 2:
                    sym0, pct0, n0 = triples[0]
                    sym1, pct1, n1 = triples[1]
                    fund_rows.append({
                        "as_of_month": month_tokens[1],
                        "report_month": report_month,
                        "report_pub_date": pub_iso,
                        "fund_name": fund_name,
                        "amc": current_amc,
                        "fund_size_pkr_thousand": fund_size,
                        "pct_cash":   asset_split["cash"],
                        "pct_equity": asset_split["equity"],
                        "pct_tbills": asset_split["tbills"],
                        "pct_others": asset_split["others"],
                        "symbol": sym0,
                        "pct_of_fund": pct0,
                        "n_shares_000": n0,
                        "section": "current",
                        "source_pdf": pdf_path.name,
                    })
                    fund_rows.append({
                        "as_of_month": month_tokens[0],
                        "report_month": report_month,
                        "report_pub_date": pub_iso,
                        "fund_name": fund_name,
                        "amc": current_amc,
                        "fund_size_pkr_thousand": fund_size,
                        "pct_cash":   asset_split["cash"],
                        "pct_equity": asset_split["equity"],
                        "pct_tbills": asset_split["tbills"],
                        "pct_others": asset_split["others"],
                        "symbol": sym1,
                        "pct_of_fund": pct1,
                        "n_shares_000": n1,
                        "section": "prior",
                        "source_pdf": pdf_path.name,
                    })
                else:
                    # Single-column or partial line -- attribute all
                    # triples to the current (report) month.
                    for sym, pct, n in triples:
                        fund_rows.append({
                            "as_of_month": (month_tokens[1] if month_tokens
                                              else report_month),
                            "report_month": report_month,
                            "report_pub_date": pub_iso,
                            "fund_name": fund_name,
                            "amc": current_amc,
                            "fund_size_pkr_thousand": fund_size,
                            "pct_cash":   asset_split["cash"],
                            "pct_equity": asset_split["equity"],
                            "pct_tbills": asset_split["tbills"],
                            "pct_others": asset_split["others"],
                            "symbol": sym,
                            "pct_of_fund": pct,
                            "n_shares_000": n,
                            "section": "current",
                            "source_pdf": pdf_path.name,
                        })
    return fund_rows, summary_rows


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _write_parquet_idempotent(rows: list[dict], path: Path,
                                 unique_keys: list[str]) -> int:
    """Read existing parquet (if any), upsert ``rows`` keyed by
    ``unique_keys``, write back. Returns number of NEW rows."""
    import pandas as pd
    new = pd.DataFrame(rows)
    if new.empty:
        return 0
    if path.exists():
        old = pd.read_parquet(path)
        # Build composite key on both
        for df in (old, new):
            df["_k"] = df[unique_keys].astype(str).agg("|".join, axis=1)
        merged = (pd.concat([old, new])
                    .drop_duplicates(subset=["_k"], keep="last")
                    .drop(columns=["_k"]))
    else:
        merged = new
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return len(new)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-download", action="store_true",
                     help="Parse what's already on disk; don't fetch.")
    ap.add_argument("--validate", action="store_true",
                     help="Validate the parquets and exit.")
    ap.add_argument("--add", metavar="HASH:YYYY-MM",
                     help="Add a new (objectid, month) and ingest.")
    ap.add_argument("--force", action="store_true",
                     help="Re-download even if file exists.")
    args = ap.parse_args()

    if args.validate:
        return _validate()

    urls = list(KNOWN_URLS)
    if args.add:
        try:
            h, m = args.add.split(":")
            urls.append((h.strip(), m.strip() + "-01"))
        except ValueError:
            print("--add expects HASH:YYYY-MM")
            return 1

    seen: list[dict] = []
    fund_rows_total: list[dict] = []
    summary_rows_total: list[dict] = []
    for hash_str, report_month in urls:
        pub = _pub_date_from_hash(hash_str)
        pub_iso = pub.date().isoformat() if pub else None
        seen.append({
            "hash": hash_str,
            "report_month": report_month,
            "pub_date": pub_iso,
            "url": AHL_BASE.format(hash=hash_str),
        })
        print(f"\n== {report_month}  hash={hash_str[:8]}  pub={pub_iso} ==")
        if args.no_download:
            pdf_path = RAW_DIR / _filename_for(hash_str, report_month)
            if not pdf_path.exists():
                print(f"  skipping (not on disk and --no-download)")
                continue
        else:
            pdf_path = _download_one(hash_str, report_month, force=args.force)
            if pdf_path is None:
                continue
        try:
            funds, summary = _parse_pdf(pdf_path, report_month, pub)
        except Exception as e:
            print(f"  parse FAILED: {type(e).__name__}: {e}")
            continue
        # Sentinel: _parse_pdf returns (None, None) when the PDF is not
        # an MF Holdings report. Move it out of the way (the file handle
        # is now closed by the with-block in _parse_pdf) and skip.
        if funds is None and summary is None:
            _move_to_market_reports(Path(pdf_path))
            continue
        print(f"  parsed: {len(funds):,} fund-holding rows, "
               f"{len(summary):,} summary rows")
        fund_rows_total.extend(funds)
        summary_rows_total.extend(summary)

    _persist_url_index(seen)

    if fund_rows_total:
        n = _write_parquet_idempotent(
            fund_rows_total, OUT_PARQUET,
            unique_keys=["as_of_month", "fund_name", "symbol", "section"],
        )
        print(f"\nfund-holdings parquet:  {n:,} rows upserted -> {OUT_PARQUET.name}")
    if summary_rows_total:
        n = _write_parquet_idempotent(
            summary_rows_total, SUMMARY_PARQUET,
            unique_keys=["as_of_month", "symbol"],
        )
        print(f"summary parquet:        {n:,} rows upserted -> {SUMMARY_PARQUET.name}")

    return _validate()


def _validate() -> int:
    import pandas as pd
    print("\n== validation ==")
    rc = 0
    if OUT_PARQUET.exists():
        df = pd.read_parquet(OUT_PARQUET)
        months = sorted(df["as_of_month"].dropna().unique())
        funds = df["fund_name"].dropna().nunique()
        symbols = df["symbol"].dropna().nunique()
        print(f"  fund-holdings: {len(df):,} rows, {funds} funds, "
               f"{symbols} symbols, {len(months)} months")
        print(f"     months: {months[:6]}{'...' if len(months) > 6 else ''}"
               f"{months[-3:]}")
    else:
        print("  fund-holdings parquet: MISSING")
        rc = 1
    if SUMMARY_PARQUET.exists():
        df = pd.read_parquet(SUMMARY_PARQUET)
        months = sorted(df["as_of_month"].dropna().unique())
        symbols = df["symbol"].dropna().nunique()
        print(f"  summary:       {len(df):,} rows, {symbols} symbols, "
               f"{len(months)} months")
    else:
        print("  summary parquet: MISSING")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
