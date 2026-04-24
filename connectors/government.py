"""Government & regulatory connectors — FBR, MoC, PBS, IMF country page."""

from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectionResult, FetchResult


class FBRRevenueConnector(BaseConnector):
    name = "FBR Revenue Collections"
    category = "macro-fiscal"
    layer = "Layer 1 — Macro (Fiscal)"
    url = "https://www.fbr.gov.pk/revenue-collections/131355"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=15,
                )
                return {
                    "status_code": r.status_code,
                    "content_len": len(r.text),
                    "keyword": "revenue" in r.text.lower(),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["status_code"] == 200 and sample["keyword"]
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"HTTP {sample['status_code']}",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


    # Only match titles that actually describe revenue collection events —
    # exclude budget proposals, anomaly committees, finance acts, etc.
    _REVENUE_RE = re.compile(
        r"(FBR\s+collects|revenue\s+collection|tax\s+collection|"
        r"collection.*(?:Rs\.?|PKR|trillion|billion)|"
        r"(?:monthly|quarterly|annual).*(?:revenue|collection))",
        re.I,
    )
    _REVENUE_EXCLUDE_RE = re.compile(
        r"budget\s+propos|finance\s+act|anomaly\s+committee|ordinance|SRO",
        re.I,
    )

    def fetch(self) -> FetchResult:
        """Discover actual revenue-collection press releases on the FBR site.

        The landing page is mostly budget-cycle noise; we filter aggressively
        for monthly/annual revenue-collection announcements and dedupe.
        """
        start = time.perf_counter()
        try:
            r = requests.get("https://www.fbr.gov.pk/", headers=self.DEFAULT_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            records: list[dict] = []
            seen: set[str] = set()
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(" ", strip=True)
                if not text or len(text) < 12:
                    continue
                if self._REVENUE_EXCLUDE_RE.search(text):
                    continue
                if not self._REVENUE_RE.search(text):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                records.append({"title": text[:200], "url": href})
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=True,  # reachable even if no releases on the landing page today
                latency_ms=elapsed,
                format="table",
                schema=["title", "url"],
                records=records[:20],
                summary=(
                    f"{len(records)} revenue-collection releases on FBR landing page "
                    "(budget/finance-act docs excluded)"
                ),
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class MoCTradeConnector(BaseConnector):
    """Ministry of Commerce — BLOCKED by Cloudflare/WAF for programmatic access.

    Returns HTTP 403 for requests-based access (even with full browser headers
    and session cookies). To actually pull this data you need either:
      - Playwright/Selenium (headless browser), or
      - Manual PDF download + scheduled job.

    Recommended alternative: Pakistan Bureau of Statistics (PBS) publishes the
    same monthly foreign-trade data freely via
    https://www.pbs.gov.pk/monthly-advance-releases-on-foreign-trade-statistics
    """

    name = "MoC Monthly Trade Statements"
    category = "macro-trade"
    layer = "Layer 1 — Macro"
    url = "https://www.commerce.gov.pk/monthly-statements/"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=15,
                )
                return {
                    "status_code": r.status_code,
                    "content_len": len(r.text),
                    "export_keyword": "export" in r.text.lower(),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["status_code"] == 200 and sample["export_keyword"]
            note = (
                f"HTTP {sample['status_code']} — "
                "Cloudflare-blocked; use PBS trade stats or headless browser"
            )
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=note,
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


    def fetch(self) -> FetchResult:
        """MoC is Cloudflare-blocked. Return the block signal explicitly."""
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=12)
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=False,
                latency_ms=elapsed,
                format="text",
                records=[],
                extras={"status_code": r.status_code},
                summary=f"HTTP {r.status_code} — Cloudflare WAF. Use PBS or headless browser.",
                error=f"HTTP {r.status_code}",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class PBSTradeStatsConnector(BaseConnector):
    """PBS monthly foreign trade statistics — alternative to blocked MoC."""

    name = "PBS Trade Statistics"
    category = "macro-trade"
    layer = "Layer 1 — Macro"
    url = "https://www.pbs.gov.pk/external-trade-statistics/"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=15,
                )
                return {
                    "status_code": r.status_code,
                    "content_len": len(r.text),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["status_code"] in (200, 301, 302)
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"HTTP {sample['status_code']}",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


    # Tight filter: we want PDFs whose name/title actually references trade
    # statistics (not census, population, or navigation links).
    _TRADE_RE = re.compile(
        r"(export|import|balance\s+of\s+trade|foreign\s+trade|"
        r"monthly\s+bulletin|trade\s+statistics|trade\s+summary)",
        re.I,
    )

    def fetch(self) -> FetchResult:
        """Extract monthly/annual trade-statistics publication links.

        Strict match on trade keywords + dedupe on URL to drop the census,
        population, and repeated navigation links that polluted earlier runs.
        """
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            records: list[dict] = []
            seen: set[str] = set()
            for a in soup.find_all("a", href=True):
                text = a.get_text(" ", strip=True)
                href = a["href"].strip()
                if not text or len(text) < 10 or href in seen:
                    continue
                if not self._TRADE_RE.search(text):
                    continue
                seen.add(href)
                records.append({"title": text[:200], "url": href})
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                format="table",
                schema=["title", "url"],
                records=records[:30],
                summary=f"{len(records)} trade-statistics publications (deduped)",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class PBSConnector(BaseConnector):
    name = "PBS (Bureau of Statistics)"
    category = "macro-real-economy"
    layer = "Layer 1 — Macro"
    url = "https://www.pbs.gov.pk/"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=15,
                )
                return {
                    "status_code": r.status_code,
                    "content_len": len(r.text),
                    "pbs_keyword": "statistics" in r.text.lower(),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["status_code"] == 200
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"HTTP {sample['status_code']}",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


    # CPI, SPI, LSM, WPI — the four real-economy releases that move PSX.
    _PBS_INCLUDE_RE = re.compile(
        r"(CPI|Consumer\s+Price|SPI|Sensitive\s+Price|WPI|Wholesale\s+Price|"
        r"LSM|Large\s+Scale\s+Manufacturing|Inflation|"
        r"Monthly\s+Bulletin|Price\s+Indicator|week\s+ended|"
        r"Monthly\s+Advance\s+Release|FY\s*\d)",
        re.I,
    )
    # Exclude census, population, agriculture, labor — those don't move PSX.
    _PBS_EXCLUDE_RE = re.compile(
        r"(census|population|housing|demograph|agriculture|labour\s+force|"
        r"employment|mining|tourism)",
        re.I,
    )

    def fetch(self) -> FetchResult:
        """Find real CPI/SPI/LSM releases on the PBS landing page.

        Requires a dated/FY-tagged title or one of the 4 release acronyms
        so navigation chrome and generic section links get dropped.
        """
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            records: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for a in soup.find_all("a", href=True):
                text = a.get_text(" ", strip=True)
                href = a["href"].strip()
                if not text or len(text) < 12:
                    continue
                if self._PBS_EXCLUDE_RE.search(text):
                    continue
                if not self._PBS_INCLUDE_RE.search(text):
                    continue
                key = (text[:100].lower(), href)
                if key in seen:
                    continue
                seen.add(key)
                records.append({"title": text[:200], "url": href})
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=bool(records),
                latency_ms=elapsed,
                format="table",
                schema=["title", "url"],
                records=records[:20],
                summary=f"{len(records)} PBS releases (CPI/SPI/LSM, deduped)",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class IMFPakistanConnector(BaseConnector):
    name = "IMF Pakistan Country Page"
    category = "political-imf"
    layer = "Layer 2 — Political"
    url = "https://www.imf.org/en/Countries/PAK"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=15,
                )
                return {
                    "status_code": r.status_code,
                    "content_len": len(r.text),
                    "pakistan_keyword": "pakistan" in r.text.lower(),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["status_code"] == 200 and sample["pakistan_keyword"]
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"HTTP {sample['status_code']}",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    # Accept Pakistan-relevant links if they mention the country (in text OR
    # URL path, e.g. /country/pak) plus either a program keyword or a
    # country-specific path pattern. Drop only obvious junk.
    _IMF_PAK_RE = re.compile(r"(pakistan|/pak\b|/PAK)", re.I)
    _IMF_RELEVANT_RE = re.compile(
        r"(mission|review|tranche|EFF|SBA|RFI|disbursement|program|"
        r"press\s+release|staff\s+report|financing|bailout|country|"
        r"board\s+discussion|FAQ)",
        re.I,
    )
    _IMF_JUNK_RE = re.compile(
        r"(springer|journal\s+of|link\.springer)", re.I,
    )

    # IMF WAF is stricter than others — mimic a real Chrome request fully.
    _IMF_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }

    def fetch(self) -> FetchResult:
        """Extract Pakistan-specific IMF program links.

        IMF's WAF occasionally 403s — we use browser-like headers and return
        a clear 'blocked' signal if that happens, so the bot can fall back on
        RSS news coverage (which already reports IMF developments promptly).
        """
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self._IMF_HEADERS, timeout=15)
            elapsed = (time.perf_counter() - start) * 1000.0
            if r.status_code == 403:
                return FetchResult(
                    name=self.name, ok=False, latency_ms=elapsed,
                    format="text", records=[],
                    extras={"status_code": 403},
                    summary="HTTP 403 — WAF blocked. Fallback: monitor IMF stories via RSS feeds.",
                    error="HTTP 403",
                )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            records: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for a in soup.find_all("a", href=True):
                text = a.get_text(" ", strip=True)
                href = a["href"]
                if not text or len(text) < 6:
                    continue
                combined = text + " " + href
                if not self._IMF_PAK_RE.search(combined):
                    continue
                if not self._IMF_RELEVANT_RE.search(combined):
                    continue
                if self._IMF_JUNK_RE.search(combined):
                    continue
                key = (text[:120].lower(), href)
                if key in seen:
                    continue
                seen.add(key)
                records.append({"title": text[:200], "url": href})
            return FetchResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                format="table",
                schema=["title", "url"],
                records=records[:15],
                summary=f"{len(records)} Pakistan IMF program/country links (deduped)",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )
