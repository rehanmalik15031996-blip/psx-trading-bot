"""PSX Financial Results & Announcements connector.

Scrapes the public PSX Data Portal company page
    https://dps.psx.com.pk/company/<SYMBOL>
and returns the table of disclosures from the *Financial Results* tab,
plus per-document PDF downloads.

Why this matters
----------------
Quarterly + annual filings carry the **Director's Report / Director's
Review** — a free-text section where management states their **outlook**,
**capex / expansion plans**, **risk factors**, and **guidance for the
next 6-12 months**. This is leading information that doesn't show up in
prices, news, or fundamentals for weeks. We cache the PDFs and let an
LLM extractor (`scripts/extract_director_report.py`) turn the prose
into structured tone + plans + risks signals.

Public API
----------
    PSXResultsConnector().fetch_announcements(symbol="HUBC")
        -> list[dict]                # one entry per filing

    PSXResultsConnector().fetch_financials_summary(symbol="HUBC")
        -> {"annual": [...], "quarterly": [...]}

    PSXResultsConnector().download_pdf(doc_id=274797, dest=Path(...))
        -> str                       # sha256 hash of the saved file

Disclosure schema
-----------------
{
    "symbol":  "HUBC",
    "date":    "2026-04-22",      # ISO
    "title":   "Financial Results for the Third Quarter Ended ...",
    "doc_id":  "274797",          # PSX document id
    "pdf_url": "https://dps.psx.com.pk/download/document/274797.pdf",
    "tab":     "Financial Results",  # tab name on PSX page
    "type":    "QUARTERLY",       # ANNUAL | HALF_YEAR | QUARTERLY
                                  # | MATERIAL | DIVIDEND | BRIEFING
                                  # | SHARIAH | OTHER
    "fy_period": "Q3 FY26",       # best-effort parsed period label
}
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectionResult, FetchResult


_PSX_BASE = "https://dps.psx.com.pk"
_DATE_FMT_IN = "%b %d, %Y"
_DATE_FMT_OUT = "%Y-%m-%d"


# ----- title-to-type classifier ---------------------------------------
_TITLE_RULES = [
    # (regex, type, fy_extractor)
    (re.compile(r"\bannual\s+report\b|annual\s+audited\s+accounts",
                re.I), "ANNUAL"),
    (re.compile(r"\bhalf[- ]year(?:ly)?\b|six\s+months\s+ended", re.I),
        "HALF_YEAR"),
    (re.compile(r"\bquarter(?:ly)?\b|q[1-3]\b|first\s+quarter|"
                r"second\s+quarter|third\s+quarter", re.I), "QUARTERLY"),
    (re.compile(r"\bfinancial\s+result", re.I), "QUARTERLY"),
    (re.compile(r"book\s+closure|notice\s+of\s+dividend", re.I),
        "DIVIDEND"),
    (re.compile(r"corporate\s+briefing|investor\s+presentation", re.I),
        "BRIEFING"),
    (re.compile(r"shariah\s+disclosure", re.I), "SHARIAH"),
    (re.compile(r"material\s+information|mat\.\s+info", re.I),
        "MATERIAL"),
]


def _classify(title: str) -> str:
    for rx, tp in _TITLE_RULES:
        if rx.search(title):
            return tp
    return "OTHER"


_QUARTER_RX = re.compile(
    r"(?:q([1-3])|first|second|third|half[- ]year|annual|year)",
    re.I,
)
_YEAR_RX = re.compile(r"(20\d{2})")


def _period_label(title: str, doc_type: str) -> str:
    """Best-effort 'Q3 FY26' / 'H1 FY26' / 'FY25' label from title."""
    yrs = _YEAR_RX.findall(title)
    fy = f"FY{yrs[-1][-2:]}" if yrs else ""
    t = title.lower()
    if doc_type == "ANNUAL":
        return f"{fy}".strip() or "Annual"
    if doc_type == "HALF_YEAR":
        return f"H1 {fy}".strip()
    if doc_type == "QUARTERLY":
        if "third" in t or "q3" in t:
            return f"Q3 {fy}".strip()
        if "second" in t or "q2" in t:
            return f"Q2 {fy}".strip()
        if "first" in t or "q1" in t:
            return f"Q1 {fy}".strip()
        return f"Q? {fy}".strip()
    return ""


# ----- connector ------------------------------------------------------
class PSXResultsConnector(BaseConnector):
    name = "PSX Financial Results & Announcements"
    category = "fundamentals"
    layer = "Layer 4 — Fundamentals (forward-looking)"
    url = f"{_PSX_BASE}/company"

    TIMEOUT = 25

    # ---- health check -----------------------------------------------
    def test(self) -> ConnectionResult:
        try:
            t0 = time.perf_counter()
            r = requests.get(
                f"{self.url}/HUBC",
                headers=self.DEFAULT_HEADERS,
                timeout=self.TIMEOUT,
            )
            r.raise_for_status()
            elapsed = (time.perf_counter() - t0) * 1000.0
            soup = BeautifulSoup(r.text, "html.parser")
            sec = soup.find(id="announcements")
            ann_table_rows = 0
            if sec is not None:
                ann_table_rows = sum(len(t.find_all("tr"))
                                      for t in sec.find_all("table"))
            return ConnectionResult(
                name=self.name, ok=ann_table_rows > 0,
                latency_ms=elapsed,
                sample={"announcement_rows": ann_table_rows},
                notes=f"HUBC has {ann_table_rows} announcement rows",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    # ---- announcements ----------------------------------------------
    def fetch_announcements(self, symbol: str,
                             tabs: tuple[str, ...] = ("Financial Results",
                                                       "Others")
                             ) -> list[dict]:
        """Return all announcements for `symbol` from the listed PSX tabs.

        Default tabs include "Financial Results" (quarterly + annual)
        and "Others" (Material Information, M&A, dividends — the lighter
        forward-looking signals). "Board Meetings" is skipped since it
        rarely contains useful prose.
        """
        url = f"{self.url}/{symbol}"
        r = requests.get(url, headers=self.DEFAULT_HEADERS,
                          timeout=self.TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        sec = soup.find(id="announcements")
        if sec is None:
            return []

        out: list[dict] = []
        # Each tab panel is <div class="tabs__panel" data-name="...">
        for panel in sec.find_all("div", class_="tabs__panel"):
            tab_name = panel.get("data-name", "")
            if tab_name not in tabs:
                continue
            for tr in panel.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if len(cells) < 3:
                    continue
                # Skip header row.
                if cells[0].get_text(strip=True).lower() == "date":
                    continue
                date_str = cells[0].get_text(strip=True)
                title = " ".join(cells[1].get_text(" ", strip=True).split())
                # The PDF link sits in the third cell; only one matters.
                pdf_a = None
                for a in cells[2].find_all("a"):
                    href = a.get("href", "")
                    if href.endswith(".pdf"):
                        pdf_a = href
                        break
                if pdf_a is None:
                    continue
                # Parse date + classify + extract doc_id.
                try:
                    iso_date = datetime.strptime(
                        date_str, _DATE_FMT_IN).strftime(_DATE_FMT_OUT)
                except ValueError:
                    iso_date = date_str  # keep raw if parse fails
                doc_id = pdf_a.rsplit("/", 1)[-1].split(".", 1)[0]
                doc_type = _classify(title)
                period = _period_label(title, doc_type)
                pdf_url = (pdf_a if pdf_a.startswith("http")
                            else f"{_PSX_BASE}{pdf_a}")
                out.append({
                    "symbol": symbol,
                    "date": iso_date,
                    "title": title,
                    "doc_id": doc_id,
                    "pdf_url": pdf_url,
                    "tab": tab_name,
                    "type": doc_type,
                    "fy_period": period,
                })
        # Newest-first.
        out.sort(key=lambda x: x.get("date", ""), reverse=True)
        return out

    # ---- summary financials ----------------------------------------
    def fetch_financials_summary(self, symbol: str) -> dict:
        """Pull the Annual + Quarterly summary table from the
        `#financials` section. Useful as a sanity check vs yfinance —
        PSX numbers are PKR thousands, so we convert to PKR mn.

        Returns:
            {
              "annual":    [{"period": "2025", "sales_mn_pkr": ..., "pat_mn_pkr": ..., "eps": ...}, ...],
              "quarterly": [{"period": "Q3 2026", ...}, ...],
            }
        """
        url = f"{self.url}/{symbol}"
        r = requests.get(url, headers=self.DEFAULT_HEADERS,
                          timeout=self.TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        out: dict = {"annual": [], "quarterly": []}
        sec = soup.find(id="financials")
        if sec is None:
            return out

        for panel in sec.find_all("div", class_="tabs__panel"):
            kind = panel.get("data-name", "").strip().lower()  # "Annual"/"Quarterly"
            if kind not in ("annual", "quarterly"):
                continue
            tables = panel.find_all("table")
            if not tables:
                continue
            tbl = tables[0]
            head_cells = tbl.find("tr").find_all(["th", "td"])
            periods = [c.get_text(strip=True)
                        for c in head_cells[1:] if c.get_text(strip=True)]
            row_map: dict[str, list[float | None]] = {}
            for tr in tbl.find_all("tr")[1:]:
                cells = tr.find_all(["td", "th"])
                if not cells:
                    continue
                label = cells[0].get_text(strip=True)
                if not label:
                    continue
                vals: list[float | None] = []
                for c in cells[1:]:
                    txt = c.get_text(strip=True).replace(",", "")
                    try:
                        vals.append(float(txt) if txt else None)
                    except ValueError:
                        vals.append(None)
                row_map[label] = vals

            for i, period in enumerate(periods):
                def _v(label: str, idx: int) -> float | None:
                    vals = row_map.get(label) or []
                    return vals[idx] if idx < len(vals) else None

                # PSX gives values in PKR '000 except EPS — convert to PKR mn.
                sales = _v("Sales", i)
                pat = _v("Profit after Taxation", i)
                eps = _v("EPS", i)
                rec = {
                    "period": period,
                    "sales_mn_pkr":
                        round(sales / 1000.0, 2) if sales else None,
                    "pat_mn_pkr":
                        round(pat / 1000.0, 2) if pat else None,
                    "eps": eps,
                }
                out[kind].append(rec)
        return out

    # ---- pdf download ----------------------------------------------
    def download_pdf(self, doc_id: str | int, dest: Path) -> str:
        """Download the PDF for `doc_id`, save to `dest`, return SHA256.

        Skips the download (and returns the existing hash) if `dest`
        already exists — keeps cache idempotent.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            return _sha256(dest)
        url = f"{_PSX_BASE}/download/document/{doc_id}.pdf"
        r = requests.get(url, headers=self.DEFAULT_HEADERS,
                          timeout=self.TIMEOUT, stream=True)
        r.raise_for_status()
        h = hashlib.sha256()
        with dest.open("wb") as fp:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fp.write(chunk)
                    h.update(chunk)
        return h.hexdigest()

    # ---- bulk fetch (universe) -------------------------------------
    def fetch(self, symbols: list[str] | None = None) -> FetchResult:
        if symbols is None:
            from config.universe import symbols as universe_symbols
            symbols = universe_symbols()

        t0 = time.perf_counter()
        all_rows: list[dict] = []
        errors: list[str] = []
        per: dict[str, int] = {}
        for sym in symbols:
            try:
                rows = self.fetch_announcements(sym)
                all_rows.extend(rows)
                per[sym] = len(rows)
            except Exception as e:
                errors.append(f"{sym}: {type(e).__name__}: {e}")
                per[sym] = 0
        elapsed = (time.perf_counter() - t0) * 1000.0
        return FetchResult(
            name=self.name, ok=bool(all_rows), latency_ms=elapsed,
            format="json",
            schema=["symbol", "date", "title", "doc_id", "pdf_url",
                     "tab", "type", "fy_period"],
            records=all_rows,
            extras={"per_symbol_counts": per, "errors": errors},
            summary=(
                f"{len(all_rows)} announcements across {len(symbols)} "
                f"symbols; errors={len(errors)}"
            ),
            error="; ".join(errors) if not all_rows else None,
        )


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fp:
        for chunk in iter(lambda: fp.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
