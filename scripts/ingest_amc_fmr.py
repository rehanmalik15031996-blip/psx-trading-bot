"""Ingest monthly Fund Manager Reports (FMRs) from Pakistani AMC websites.

Why this exists
---------------
The AHL "Mutual Funds Equity Holdings" PDF (scripts/ingest_ahl_mf_holdings.py)
stopped publishing per-fund detail in the public ``path=178`` namespace
after Jun-2025. As of May-2026 our per-fund holdings parquet is 344+ days
stale.

Each major AMC publishes its OWN monthly Fund Manager Report (FMR) on its
website with per-fund top-10 equity holdings (stock name + % of total
assets). We scrape these directly, getting fresher and more granular data
than AHL ever provided.

Architecture
------------
1. **AMC registry** (AMC_SOURCES): each AMC has either a predictable URL
   pattern (Al Meezan) or a listing-page scraper (Lucky, NBP, ...).
2. **Discovery**: for each AMC + month, build/find the FMR PDF URL.
3. **Download**: cache to ``data/raw/amc_fmr/<amc_slug>/<YYYY-MM>.pdf``
   (idempotent -- skip if already on disk and >50 KB).
4. **Parse**: pdfplumber-based table extraction for "Top Ten Equity
   Holdings" tables. Fund name comes from the page header preceding
   each table.
5. **Map**: company_name -> PSX symbol via curated COMPANY_TO_SYMBOL
   dict + token-based fallback for our universe.
6. **Upsert**: write to ``data/flows/amc_fmr_holdings.parquet`` with
   schema compatible with ``mutual_fund_holdings.parquet`` so
   ``brain/mf_flows.py`` can union both sources.

Schema (long-format)
--------------------
    as_of_month     YYYY-MM-01 (month the report COVERS)
    report_pub_date YYYY-MM-DD (publication date, best-effort)
    amc             "Lucky Investments Limited"
    fund_name       "Lucky Islamic Stock Fund"
    symbol          PSX ticker, or "" if unmapped
    stock_name_raw  raw company name from PDF
    pct_of_fund     0-100, share of fund AUM
    rank_in_fund    1-10
    source_pdf      filename basename
    source_url      original URL

CLI
---
    python scripts/ingest_amc_fmr.py                  # discover + download + parse
    python scripts/ingest_amc_fmr.py --months 2026-04 2026-03  # specific months
    python scripts/ingest_amc_fmr.py --amc lucky almeezan      # specific AMCs
    python scripts/ingest_amc_fmr.py --validate                # show coverage stats
    python scripts/ingest_amc_fmr.py --show-symbol OGDC        # per-symbol drill-down
"""
from __future__ import annotations

import argparse
import calendar
import io
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_ROOT = ROOT / "data" / "raw" / "amc_fmr"
OUT_PARQUET = ROOT / "data" / "flows" / "amc_fmr_holdings.parquet"
# URL index lives outside data/raw/ so it gets tracked in git (provides
# an audit trail of which PDFs we've downloaded across runs without
# bloating the repo with the actual PDFs).
URL_INDEX = ROOT / "data" / "flows" / "amc_fmr_url_index.json"

UA = {"User-Agent": "Mozilla/5.0 (compatible; psx-bot/1.0)"}

# ---------------------------------------------------------------------------
# AMC source registry
# ---------------------------------------------------------------------------
# Each entry has:
#   slug            short identifier
#   amc             official AMC name (matches KNOWN_AMCS in AHL parser)
#   month_format    string format spec for the month token in URL
#   discover_fn     callable(year, month) -> list of (url, hint) candidates
#                   (multiple URLs per month can be returned; first 200 wins)
#   notes           free text
# ---------------------------------------------------------------------------

_MONTH_NAMES_FULL = ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November",
                     "December"]
_MONTH_NAMES_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _almeezan_urls(year: int, month: int) -> list[str]:
    """Al Meezan publishes at:
    https://www.almeezangroup.com/assets/uploads/{upload}/FMR-{Month}-{Year}.pdf

    Counter-intuitively, Al Meezan uses ONE upload folder for many months
    (a yearly bulk-upload pattern). Empirical findings (probed 2026-05-11):

      2026 reports (Jan-Apr) all live under upload=2026/02
      2025 Dec lives under upload=2026/01
      2025 Sep-Nov live under upload=2025/02

    We prepend these proven historical paths, then probe forward-rolling
    "publication month" guesses as a fallback for future months.
    """
    full = _MONTH_NAMES_FULL[month - 1]
    upload_candidates: list[str] = []

    # Proven historical paths first (year + special-case rules)
    if year == 2026:
        upload_candidates.append("2026/02")
        upload_candidates.append("2026/01")
    if year == 2025 and month == 12:
        upload_candidates.append("2026/01")
    if year == 2025 and month <= 11:
        upload_candidates.extend(["2025/02", "2025/01"])

    # Then forward-rolling guess (publication is usually next month)
    for offset in range(0, 4):
        y, m = year, month + offset
        while m > 12:
            m -= 12
            y += 1
        token = f"{y:04d}/{m:02d}"
        if token not in upload_candidates:
            upload_candidates.append(token)

    # Final catch-all fallbacks
    for token in ("2026/02", "2025/02", "2024/12"):
        if token not in upload_candidates:
            upload_candidates.append(token)

    base = "https://www.almeezangroup.com/assets/uploads"
    return [f"{base}/{up}/FMR-{full}-{year}.pdf" for up in upload_candidates]


def _lucky_urls(year: int, month: int) -> list[str]:
    """Lucky Investments has an HTML listing page; we additionally try a
    few naming conventions seen on disk."""
    abbr = _MONTH_NAMES_ABBR[month - 1]
    full = _MONTH_NAMES_FULL[month - 1]
    paths = [
        f"https://luckyinvestments.com.pk/wp-content/uploads/{year}/{month + 1:02d}/FMR-{abbr}-{year}-1.pdf",
        f"https://luckyinvestments.com.pk/wp-content/uploads/{year}/{month + 1:02d}/FMR-{abbr}-{year}.pdf",
        f"https://luckyinvestments.com.pk/wp-content/uploads/{year}/{month + 1:02d}/FMR-{full}-{year}.pdf",
        f"https://luckyinvestments.com.pk/wp-content/uploads/lucky_downloads/FMR-{abbr}-{year}.pdf",
        f"https://luckyinvestments.com.pk/wp-content/uploads/lucky_downloads/FMR-{full}-{year}.pdf",
    ]
    # Edge-case Apr 2026 publication folder = May, etc.
    if month == 12:
        paths.append(
            f"https://luckyinvestments.com.pk/wp-content/uploads/{year + 1}/01/FMR-{abbr}-{year}-1.pdf")
    return paths


# Add additional AMCs incrementally; these stubs document the next round
# of work but are disabled for now (their discovery is more involved).
def _nbp_urls(year: int, month: int) -> list[str]:
    """NBP Funds: https://www.nbpfunds.com/wp-content/uploads/{Y}/{M:02d}/
    Complete-FMR-Conventional-{Month}-{Year}.pdf
    The upload month is usually the next calendar month."""
    full = _MONTH_NAMES_FULL[month - 1]
    nm_y, nm_m = (year + 1, 1) if month == 12 else (year, month + 1)
    paths = []
    for up_y, up_m in [(nm_y, nm_m), (year, month), (nm_y, min(12, nm_m + 1))]:
        paths.append(
            f"https://www.nbpfunds.com/wp-content/uploads/{up_y}/{up_m:02d}/"
            f"Complete-FMR-Conventional-{full}-{year}.pdf")
        paths.append(
            f"https://www.nbpfunds.com/wp-content/uploads/{up_y}/{up_m:02d}/"
            f"Complete-FMR-Islamic-{full}-{year}.pdf")
    return paths


AMC_SOURCES: list[dict] = [
    {
        "slug": "lucky",
        "amc": "Lucky Investments Limited",
        "discover_fn": _lucky_urls,
        "equity_funds": ["Lucky Islamic Stock Fund", "Lucky Islamic Energy Fund"],
        "notes": "URL pattern proven for Apr-2026.",
    },
    {
        "slug": "almeezan",
        "amc": "Al Meezan Investment Management Limited",
        "discover_fn": _almeezan_urls,
        "equity_funds": [],  # discovered from PDF section headers
        "notes": "URL pattern proven Sep-2025 -> Apr-2026.",
    },
    {
        "slug": "nbp",
        "amc": "NBP Fund Management Limited",
        "discover_fn": _nbp_urls,
        "equity_funds": ["NBP Stock Fund", "NAFA Stock Fund",
                          "NBP Islamic Stock Fund", "NAFA Islamic Stock Fund"],
        "notes": "URL pattern probed; may need listing-page scrape fallback.",
    },
]


# ---------------------------------------------------------------------------
# Company name -> PSX symbol mapping
# ---------------------------------------------------------------------------
# Curated from the FMR conventions we've seen. Keys are LOWERCASED stripped.
# When a name lacks a key it falls back to token-overlap with universe.
COMPANY_TO_SYMBOL: dict[str, str] = {
    # Universe (35 names)
    "the hub power company limited": "HUBC",
    "hub power company limited": "HUBC",
    "pakistan aluminium beverage cans limited": "PABC",
    "maple leaf cement factory limited": "MLCF",
    "oil & gas development company limited": "OGDC",
    "oil and gas development company limited": "OGDC",
    "faysal bank limited": "FABL",
    "pakistan petroleum limited": "PPL",
    "nishat power limited": "NPL",
    "pakistan oilfields limited": "POL",
    "fauji cement company limited": "FCCL",
    "attock petroleum limited": "APL",
    "engro polymer & chemicals limited": "EPCL",
    "engro polymer and chemicals limited": "EPCL",
    "kohat cement company limited": "KOHC",
    "the searle company limited": "SEARL",
    "searle company limited": "SEARL",
    "mcb bank limited": "MCB",
    "meezan bank limited": "MEBL",
    "pakistan state oil company limited": "PSO",
    "habib bank limited": "HBL",
    "united bank limited": "UBL",
    "bank al habib limited": "BAHL",
    "national bank of pakistan": "NBP",
    "mari petroleum company limited": "MARI",
    "mari energies limited": "MARI",
    "lucky cement limited": "LUCK",
    "d.g. khan cement company limited": "DGKC",
    "dg khan cement company limited": "DGKC",
    "fauji fertilizer company limited": "FFC",
    "engro fertilizers limited": "EFERT",
    "fatima fertilizer company limited": "FATIMA",
    "kot addu power company limited": "KAPCO",
    "k-electric limited": "KEL",
    "k electric limited": "KEL",
    "attock refinery limited": "ATRL",
    "engro corporation limited": "ENGROH",
    "engro holdings limited": "ENGROH",
    "lotte chemical pakistan limited": "LOTCHEM",
    "systems limited": "SYS",
    "trg pakistan limited": "TRG",
    "indus motor company limited": "INDU",
    "colgate palmolive pakistan limited": "COLG",
    "colgate-palmolive (pakistan) limited": "COLG",
    # Common non-universe names appearing in FMR top-10s
    "pakistan tobacco company limited": "PAKT",
    "millat tractors limited": "MTL",
    "honda atlas cars (pakistan) limited": "HCAR",
    "pak suzuki motor company limited": "PSMC",
    "thal limited": "THALL",
    "international steels limited": "ISL",
    "international industries limited": "INIL",
    "interloop limited": "ILP",
    "nishat mills limited": "NML",
    "nishat chunian limited": "NCL",
    "kohinoor textile mills limited": "KTML",
    "feroze 1888 mills limited": "FZCM",
    "service industries limited": "SRVI",
    "service global footwear limited": "SGF",
    "airlink communication limited": "AIRLINK",
    "air link communication limited": "AIRLINK",
    "tps pakistan limited": "TPS",
    "octopus digital limited": "OCTOPUS",
    "avanceon limited": "AVN",
    "netsol technologies limited": "NETSOL",
    "pakistan reinsurance company limited": "PAKRI",
    "adamjee insurance company limited": "AICL",
    "askari bank limited": "AKBL",
    "soneri bank limited": "SNBL",
    "bank alfalah limited": "BAFL",
    "bank al-falah limited": "BAFL",
    "allied bank limited": "ABL",
    "askari general insurance company limited": "AGIC",
    "atlas honda limited": "ATLH",
    "ghani glass limited": "GHGL",
    "ghani global holdings limited": "GHGL",
    "tariq glass industries limited": "TGL",
    "shifa international hospitals limited": "SHFA",
    "amreli steels limited": "ASTL",
    "agha steel industries limited": "AGHA",
    "mughal iron & steel industries limited": "MUGHAL",
    "abbott laboratories (pakistan) limited": "ABOT",
    "highnoon laboratories limited": "HINOON",
    "ferozsons laboratories limited": "FEROZ",
    "glaxosmithkline pakistan limited": "GLAXO",
    "ibl healthcare limited": "IBLHL",
    "haleon pakistan limited": "HALEON",
    "unity foods limited": "UNITY",
    "national foods limited": "NATF",
    "frieslandcampina engro pakistan limited": "FCEPL",
    "tpl properties limited": "TPLP",
    "tpl trakker limited": "TPL",
    "hascol petroleum limited": "HASCOL",
    "shell pakistan limited": "SHEL",
    "national refinery limited": "NRL",
    "byco petroleum pakistan limited": "BYCO",
    "pakistan refinery limited": "PRL",
    "sui northern gas pipelines limited": "SNGP",
    "sui southern gas company limited": "SSGC",
    "engro fertilizer limited": "EFERT",
    "fauji fertilizer bin qasim limited": "FFBL",
    "ghani chemical industries limited": "GCIL",
    "image pakistan limited": "IMAGE",
    "pakistan international container terminal": "PICT",
    "lucky core industries limited": "LCI",
    "ici pakistan limited": "ICI",
    "pakgen power limited": "PKGP",
    "lalpir power limited": "LPL",
    "saif power limited": "SPWL",
    "altern energy limited": "ALTN",
    "japan power generation limited": "JPGL",
    "kohinoor energy limited": "KOHE",
    "tplx limited": "TPLX",
    "ittefaq iron industries limited": "ITTEFAQ",
    "askari general insurance": "AGIC",
    "pakistan general insurance": "PGI",
    "habib insurance company limited": "HICL",
    "tri-pack films limited": "TRIPF",
    "pakistan cables limited": "PCAL",
    "century paper & board mills limited": "CEPB",
    "packages limited": "PKGS",
    "packages convertors limited": "PCV",
    "siemens (pakistan) engineering co. limited": "SIEM",
    "millat equipment limited": "MEQL",
    "atlas battery limited": "ATBA",
    "exide pakistan limited": "EXIDE",
    "cherat cement company limited": "CHCC",
    "pioneer cement limited": "PIOC",
    "thatta cement company limited": "TCEM",
    "gharibwal cement limited": "GWLC",
    "power cement limited": "POWER",
    "bestway cement limited": "BWCL",
    "askari cement limited": "ACL",
    "fauji foods limited": "FFL",
    "treet corporation limited": "TREET",
    "english biscuit manufacturers limited": "EBM",
    "ismail industries limited": "ISIL",
}


# Fund-of-funds / plan names that are NOT stocks. They share words with
# real company names (Meezan, Atlas, NBP, ...) so we deny-list them
# explicitly. Match is substring-on-lowered.
NOT_A_STOCK = (
    "mfpf", "msaf", "mtpf-", "mtpf -", "asset allocation plan",
    "strategic allocation plan", "strategeic allocation plan",
    "fixed term plan", "fixed return plan", "principal protected plan",
    "growth plan", "income plan", "capital protected plan",
    "pension fund - mtpf", "savings plan", "smart savings",
    "5 years peer group", "peer group average return",
    "nav per unit", "net assets", "fund net assets", "fund size",
    "expense ratio", "selling and marketing", "management fee",
    "front end load", "back end load", "contingent load",
    "leverage", "minimum investment", "subscription", "valuation days",
    "trustee", "auditor", "fund manager", "fund category", "fund type",
    "investment committee", "rating agency", "amc rating", "ticker",
    "listing", "mom %", "%mtd", "ytd", "quote", "benchmark",
    "ticker mznp", "wafi energy",  # WAFI is a private merger entity
)


def _is_not_a_stock(name_lc: str) -> bool:
    return any(bad in name_lc for bad in NOT_A_STOCK)


def map_company_to_symbol(name: str) -> tuple[str, float]:
    """Returns (symbol, confidence). Confidence 1.0 = exact dict hit;
    0.5-0.9 = tail-match or token-overlap; 0.0 = no match.

    Robust to PDF column-merge noise: when the input contains
    boilerplate text concatenated with the actual stock name (e.g.
    'Fund Manager Asif Imtiaz Pakistan Petroleum Limited'), we scan for
    the LAST occurrence of any known company name in the string."""
    norm = re.sub(r"\s+", " ", name.strip().lower()).strip(".,;:")
    norm = norm.replace("ltd.", "limited").replace("ltd", "limited")

    # 1. Exact dict hit
    if norm in COMPANY_TO_SYMBOL:
        return COMPANY_TO_SYMBOL[norm], 1.0

    # 2. Strip 'limited' suffix and retry
    norm2 = re.sub(r"\s+limited$", "", norm).strip()
    if norm2 in COMPANY_TO_SYMBOL:
        return COMPANY_TO_SYMBOL[norm2], 0.95

    # 3. Tail-match: search for any known company name as a SUFFIX of the
    # input. Picks the longest matching key (most specific). This is the
    # main fix for column-merged PDFs like Al Meezan's where boilerplate
    # text gets concatenated before the actual stock name.
    # Note: we do this BEFORE the deny-list because column-merged inputs
    # often start with metadata strings ("AMC Rating", "Trustee", ...)
    # that would otherwise short-circuit a valid match.
    best_match = ""
    best_sym = ""
    for key, sym in COMPANY_TO_SYMBOL.items():
        if norm.endswith(key) and len(key) > len(best_match):
            best_match, best_sym = key, sym
    if best_sym:
        return best_sym, 0.85

    # 4. Containment: known name appears anywhere in the input
    for key, sym in COMPANY_TO_SYMBOL.items():
        if len(key) > 12 and key in norm and len(key) > len(best_match):
            best_match, best_sym = key, sym
    if best_sym:
        return best_sym, 0.75

    # 5. Deny-list: pure-noise rows (no real stock name found above)
    if _is_not_a_stock(norm):
        return "", 0.0

    # 5. Universe token-overlap fallback (last resort)
    try:
        from config.universe import UNIVERSE
        norm_tokens = set(re.findall(r"[a-z0-9]+", norm))
        STOPS = {"limited", "company", "the", "of", "and", "pakistan",
                 "co", "ltd", "&", "fund", "manager", "report", "investment",
                 "rating", "amc", "ticker", "trustee", "auditor"}
        norm_tokens -= STOPS
        best_sym2, best_score = "", 0.0
        for u in UNIVERSE:
            uname_tokens = set(re.findall(r"[a-z0-9]+", u.name.lower()))
            uname_tokens -= STOPS
            if not uname_tokens:
                continue
            overlap = len(norm_tokens & uname_tokens)
            score = overlap / max(len(uname_tokens), 1)
            if score > best_score and overlap >= 1:
                best_sym2, best_score = u.symbol, score
        if best_score >= 0.6:  # tightened from 0.5 to avoid false positives
            return best_sym2, round(0.5 + 0.3 * best_score, 2)
    except ImportError:
        pass
    return "", 0.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _http_head_ok(url: str, timeout: int = 10) -> tuple[bool, int]:
    """Cheap existence check. Tries HEAD first, then a Range-GET (first 1KB)
    fallback for servers that 405 HEAD (e.g. Al Meezan / Cloudflare)."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True, headers=UA)
        if r.status_code == 200:
            cl = int(r.headers.get("content-length", "0") or 0)
            ct = r.headers.get("content-type", "")
            return ("pdf" in ct.lower()) and cl > 50_000, cl
        if r.status_code in (404, 403):
            return False, 0
        # 405 / 501 etc -> fall through to GET-Range
    except Exception:
        return False, 0

    try:
        h = dict(UA)
        h["Range"] = "bytes=0-1023"
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers=h, stream=True)
        try:
            chunk = r.raw.read(1024)
        finally:
            r.close()
        if r.status_code not in (200, 206):
            return False, 0
        # Content-Range header: "bytes 0-1023/<total>"
        cr = r.headers.get("content-range", "")
        cl = 0
        if "/" in cr:
            try:
                cl = int(cr.split("/")[-1])
            except ValueError:
                cl = 0
        ct = r.headers.get("content-type", "")
        # Sniff first bytes for "%PDF" magic if header is unhelpful
        is_pdf = ("pdf" in ct.lower()) or (chunk[:4] == b"%PDF")
        return is_pdf and (cl > 50_000 or cl == 0), cl
    except Exception:
        return False, 0


def _http_download(url: str, out: Path, timeout: int = 120) -> bool:
    try:
        r = requests.get(url, timeout=timeout, headers=UA, stream=True)
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        return out.stat().st_size > 50_000
    except Exception as e:
        print(f"    DOWNLOAD FAILED: {e}")
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        return False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _discover_for(amc: dict, year: int, month: int) -> str | None:
    urls = amc["discover_fn"](year, month)
    for url in urls:
        ok, cl = _http_head_ok(url)
        if ok:
            print(f"    found {amc['slug']} {year}-{month:02d}: {url}  ({cl:,} bytes)")
            return url
    return None


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------
_PCT_RE = re.compile(r"^([\d]+(?:\.\d+)?)\s*%$")
_PCT_INLINE_RE = re.compile(r"([\d]+(?:\.\d+)?)\s*%")
_FUND_HEADER_HINTS = (
    "Fund Manager Report",
    "Investment Objective",
    "Top Ten Equity Holdings",
    "Top 10 Equity Holdings",
    "Top Equity Holdings",
)


def parse_fmr_pdf(pdf_path: Path) -> list[dict]:
    """Parse a FMR PDF and yield rows, one per (fund, holding).

    Strategy:
      * pdfplumber.pages: walk through pages.
      * On each page, look for a "Top Ten Equity Holdings" or similar
        heading. The fund name is whatever non-disclaimer text appears
        on that page or the previous page's last header.
      * Extract the table beneath. Each row is (name, pct).
    """
    try:
        import pdfplumber
    except ImportError as e:
        print(f"  pdfplumber not installed: {e}")
        return []

    rows: list[dict] = []
    fund_name_carry: str | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            text_lc = text.lower()

            # Detect fund name candidates on this page. Lines like
            # "Lucky Islamic Stock Fund" or "Al Meezan Mutual Fund" appear
            # as standalone lines (no trailing %, not all-caps banner).
            page_fund_name = _detect_fund_name(text)
            if page_fund_name:
                fund_name_carry = page_fund_name

            # Does this page have a "Top Ten Equity Holdings" table?
            if not any(h.lower() in text_lc for h in
                        ("top ten equity holdings", "top 10 equity holdings",
                         "top equity holdings", "top holdings")):
                continue

            # Try table extraction first
            tables = page.extract_tables() or []
            holdings = _extract_holdings_from_tables(tables)
            if not holdings:
                holdings = _extract_holdings_from_text(text)

            if not holdings:
                continue

            fund = fund_name_carry or f"<unknown fund p{page_idx + 1}>"
            for rank, (name, pct) in enumerate(holdings, start=1):
                sym, conf = map_company_to_symbol(name)
                rows.append({
                    "fund_name": fund,
                    "stock_name_raw": name,
                    "symbol": sym,
                    "pct_of_fund": pct,
                    "rank_in_fund": rank,
                    "page": page_idx + 1,
                    "_map_confidence": conf,
                })
    return rows


_FUND_TYPE_WORDS = (
    "stock", "equity", "energy", "index", "income", "money market",
    "cash", "growth", "savings", "asset allocation", "balanced",
    "dedicated", "pension", "ijarah", "sovereign", "treasury",
    "capital protected", "fixed term", "principal protected",
    "dividend yield", "thematic", "bond",
)


def _detect_fund_name(text: str) -> str | None:
    """Find a fund header on the page. Prefer descriptive lines ending in
    'Fund' with a fund-type word like 'Stock' or 'Energy', and free of
    digits / dates / numerical metadata (which would indicate it's a row
    from a comparison table, not a header)."""
    candidates: list[tuple[int, str]] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or len(s) > 70 or len(s) < 8:
            continue
        # Reject lines with digits (rate/AUM/date rows pretending to be a header)
        if any(c.isdigit() for c in s):
            continue
        sl = s.lower()
        if not (sl.endswith(" fund") or " fund " in sl):
            continue
        bad = ("category of", "investment objective", "performance",
               "benchmark", "fund manager report", "asset allocation",
               "objective of the fund", "members of investment",
               "name of the fund", "name of shariah", "scheme of the fund",
               "compliant scheme", "the fund holds", "before making",
               "to understand", "this publication", "complete fmr",
               "fund category", "fund manager",
               "type of fund", "category of fund", "rating of fund")
        if any(b in sl for b in bad):
            continue
        if not any(t in sl for t in _FUND_TYPE_WORDS):
            continue
        if s.isupper():
            continue
        if not any(c.isupper() for c in s):
            continue
        # Score: presence of an AMC brand word boosts; "stock"/"equity" boosts
        score = len(s)
        if any(brand in sl for brand in ("lucky", "meezan", "nbp", "nafa",
                                            "ubl", "mcb", "atlas", "alfalah",
                                            "abl", "akd", "lakson", "faysal",
                                            "hbl", "js", "national investment",
                                            "ahl ", "al-falah", "askari")):
            score += 30
        if "stock" in sl or "equity" in sl or "energy" in sl or "index" in sl:
            score += 20
        candidates.append((score, s))
    if not candidates:
        return None
    candidates.sort(key=lambda t: -t[0])
    return candidates[0][1]


def _extract_holdings_from_tables(tables: list) -> list[tuple[str, float]]:
    """Look for a 2-column table where col-A is a name and col-B is X%."""
    out: list[tuple[str, float]] = []
    for tbl in tables:
        if not tbl or len(tbl) < 3:
            continue
        # Find a header row mentioning "Holdings" and "Percentage"/Assets
        header_idx = None
        for i, row in enumerate(tbl[:3]):
            row_lc = " ".join((c or "") for c in row).lower()
            if ("hold" in row_lc) and ("percent" in row_lc or "%" in row_lc
                                          or "asset" in row_lc):
                header_idx = i
                break
        if header_idx is None:
            continue
        # Read rows below header
        local: list[tuple[str, float]] = []
        for row in tbl[header_idx + 1:]:
            if not row:
                continue
            # Pick first non-empty cell as name, last non-empty as pct
            cells = [c.strip() if c else "" for c in row]
            non_empty = [c for c in cells if c]
            if len(non_empty) < 2:
                continue
            name = non_empty[0]
            pct_cell = non_empty[-1]
            m = _PCT_RE.match(pct_cell) or _PCT_INLINE_RE.search(pct_cell)
            if not m:
                continue
            try:
                pct = float(m.group(1))
            except ValueError:
                continue
            if pct <= 0 or pct > 100:
                continue
            if len(name) < 3 or name.lower() in ("total", "others", "cash"):
                continue
            local.append((name, pct))
            if len(local) >= 12:
                break
        if 3 <= len(local) <= 12:
            if not out or len(local) > len(out):
                out = local
    return out


def _extract_holdings_from_text(text: str) -> list[tuple[str, float]]:
    """Fallback when pdfplumber's table extractor misses. Scan lines for
    'Some Company Name <num>%' patterns under a Top Ten Equity Holdings
    heading."""
    lines = text.split("\n")
    in_section = False
    out: list[tuple[str, float]] = []
    for line in lines:
        sl = line.strip()
        sl_lc = sl.lower()
        if any(h in sl_lc for h in
                ("top ten equity holdings", "top 10 equity holdings",
                 "top equity holdings")):
            in_section = True
            continue
        if not in_section:
            continue
        # End of section sentinels
        if any(x in sl_lc for x in ("general information", "asset allocation",
                                       "sector allocation", "portfolio",
                                       "performance", "members of investment")):
            break
        m = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)\s*%\s*$", sl)
        if m:
            name = m.group(1).strip().rstrip("|").strip()
            try:
                pct = float(m.group(2))
            except ValueError:
                continue
            if pct <= 0 or pct > 100 or len(name) < 3:
                continue
            if name.lower() in ("total", "others", "cash"):
                continue
            out.append((name, pct))
            if len(out) >= 12:
                break
    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run(months: list[tuple[int, int]] | None = None,
        amc_slugs: list[str] | None = None) -> None:
    if months is None:
        # Default: last 6 months
        today = date.today()
        months = []
        for offset in range(0, 6):
            y, m = today.year, today.month - offset
            while m <= 0:
                m += 12
                y -= 1
            months.append((y, m))

    sources = [a for a in AMC_SOURCES
               if (amc_slugs is None or a["slug"] in amc_slugs)]
    if not sources:
        print(f"no AMC sources match {amc_slugs}")
        return

    all_rows: list[dict] = []
    seen_urls: list[dict] = []

    for amc in sources:
        slug = amc["slug"]
        amc_dir = RAW_ROOT / slug
        amc_dir.mkdir(parents=True, exist_ok=True)
        for y, m in months:
            ym = f"{y:04d}-{m:02d}"
            local = amc_dir / f"{ym}.pdf"
            print(f"\n== {amc['amc']}  {ym} ==")
            if local.exists() and local.stat().st_size > 50_000:
                print(f"  cached: {local.name} ({local.stat().st_size:,} bytes)")
                url = "<cached>"
            else:
                url = _discover_for(amc, y, m)
                if url is None:
                    print(f"  no URL discovered for {slug} {ym}")
                    continue
                if not _http_download(url, local):
                    continue
                seen_urls.append({"amc": slug, "month": ym, "url": url,
                                   "size": local.stat().st_size})

            print(f"  parsing {local.name}...")
            rows = parse_fmr_pdf(local)
            if not rows:
                print(f"  no holdings extracted from {local.name}")
                continue
            print(f"  parsed {len(rows)} holdings across "
                  f"{len({r['fund_name'] for r in rows})} fund(s)")
            for r in rows:
                r.update({
                    "amc": amc["amc"],
                    "as_of_month": f"{y:04d}-{m:02d}-01",
                    "report_pub_date": _pub_date_guess(y, m),
                    "source_pdf": local.name,
                    "source_url": url,
                })
                all_rows.append(r)

    # Persist URL index
    if seen_urls:
        existing = []
        if URL_INDEX.exists():
            try:
                existing = json.loads(URL_INDEX.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.extend(seen_urls)
        URL_INDEX.parent.mkdir(parents=True, exist_ok=True)
        URL_INDEX.write_text(json.dumps(existing, indent=2),
                              encoding="utf-8")

    if not all_rows:
        print("\nno new rows; nothing to upsert.")
        return

    # Upsert into parquet
    import pandas as pd
    df_new = pd.DataFrame(all_rows)
    # Drop unmapped (symbol == "") rows from main parquet but log count
    n_unmapped = (df_new["symbol"] == "").sum()
    df_mapped = df_new[df_new["symbol"] != ""].copy()
    print(f"\nmapped {len(df_mapped)} / {len(df_new)} rows to PSX symbols "
          f"({n_unmapped} unmapped)")

    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    if OUT_PARQUET.exists():
        df_old = pd.read_parquet(OUT_PARQUET)
        # Upsert key: (as_of_month, amc, fund_name, symbol)
        key = ["as_of_month", "amc", "fund_name", "symbol"]
        combined = pd.concat([df_old, df_mapped], ignore_index=True)
        combined = combined.drop_duplicates(subset=key, keep="last")
    else:
        combined = df_mapped
    combined.to_parquet(OUT_PARQUET, index=False)
    print(f"upsert -> {OUT_PARQUET}  ({len(combined):,} total rows)")

    # Summary
    months_in = sorted(combined["as_of_month"].unique())
    funds_in = combined["fund_name"].nunique()
    syms_in = combined["symbol"].nunique()
    print(f"\nparquet snapshot: {funds_in} funds, {syms_in} symbols, "
          f"{len(months_in)} months ({months_in[0]} -> {months_in[-1]})")


def _pub_date_guess(year: int, month: int) -> str:
    """FMRs are typically published 7-15 days after month-end."""
    last_day = calendar.monthrange(year, month)[1]
    nm_y, nm_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{nm_y:04d}-{nm_m:02d}-15"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _validate() -> None:
    if not OUT_PARQUET.exists():
        print(f"no parquet at {OUT_PARQUET}")
        return
    import pandas as pd
    df = pd.read_parquet(OUT_PARQUET)
    print(f"\namc_fmr_holdings.parquet: {len(df):,} rows")
    print(f"  months   : {sorted(df['as_of_month'].unique())}")
    print(f"  AMCs     : {sorted(df['amc'].unique())}")
    print(f"  funds    : {df['fund_name'].nunique()}")
    print(f"  symbols  : {df['symbol'].nunique()}")
    print(f"  rows/AMC :")
    for amc, n in df.groupby("amc")["symbol"].count().items():
        print(f"    {amc:<55s} {n:>5d}")


def _show_symbol(sym: str) -> None:
    if not OUT_PARQUET.exists():
        print(f"no parquet at {OUT_PARQUET}")
        return
    import pandas as pd
    df = pd.read_parquet(OUT_PARQUET)
    sub = df[df["symbol"] == sym].sort_values(["as_of_month", "fund_name"])
    if sub.empty:
        print(f"no AMC FMR rows for {sym}")
        return
    print(f"\n{sym} across {sub['fund_name'].nunique()} fund(s) and "
          f"{sub['as_of_month'].nunique()} month(s):\n")
    print(f"{'as_of':<11} {'AMC':<22} {'fund':<32} {'%':>6} {'rank':>4}")
    for _, r in sub.iterrows():
        amc = (r["amc"] or "")[:22]
        fund = (r["fund_name"] or "")[:32]
        print(f"{r['as_of_month']:<11} {amc:<22} {fund:<32} "
              f"{r['pct_of_fund']:>6.2f} {int(r.get('rank_in_fund', 0)):>4d}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--months", nargs="*",
                   help="YYYY-MM month tokens (default: last 6 months)")
    p.add_argument("--amc", nargs="*",
                   help="AMC slug filter, e.g. lucky almeezan nbp "
                        "(default: all)")
    p.add_argument("--validate", action="store_true",
                   help="print parquet coverage and exit")
    p.add_argument("--show-symbol", metavar="SYM",
                   help="print all FMR rows for one symbol and exit")
    args = p.parse_args()

    if args.validate:
        _validate()
        return
    if args.show_symbol:
        _show_symbol(args.show_symbol.upper())
        return

    months = None
    if args.months:
        months = []
        for tok in args.months:
            y, m = tok.split("-")
            months.append((int(y), int(m)))

    run(months=months, amc_slugs=args.amc)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
