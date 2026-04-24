"""SCStrade FIPI connector — daily foreign/local investor flows (scraped).

Page: http://www.scstrade.com/fipitext.aspx
"""

from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectionResult, FetchResult


class SCStradeFIPIConnector(BaseConnector):
    name = "SCStrade FIPI/LIPI"
    category = "flows"
    layer = "Layer 3 — Flows"
    url = "http://www.scstrade.com/fipitext.aspx"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=15,
                )
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                text = soup.get_text(" ", strip=True)
                keywords = [
                    "Foreign",
                    "Banks",
                    "Mutual",
                    "Insurance",
                    "Individuals",
                    "NBFC",
                ]
                hits = [kw for kw in keywords if kw in text]
                return {
                    "status_code": r.status_code,
                    "keyword_hits": hits,
                    "content_len": len(r.text),
                }

            sample, elapsed = self._timed(pull)
            ok = len(sample["keyword_hits"]) >= 3
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"{len(sample['keyword_hits'])} FIPI keywords found",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


    # Participant-level pattern: "CATEGORY B: 15.48 mn S: -15.21 mn N: 0.27 mn" (PKR mn)
    _PARTICIPANT_RE = re.compile(
        r"(?P<cat>[A-Za-z][A-Za-z /&\-]{2,40}?)\s+B:\s*(?P<buy>-?\d+\.?\d*)\s*mn\s+"
        r"S:\s*(?P<sell>-?\d+\.?\d*)\s*mn\s+N:\s*(?P<net>-?\d+\.?\d*)\s*mn"
    )

    # Sector-level pattern: "<Sector> Gross Buy USD x mn Gross Sell USD -y mn Net Buy/Sell USD z mn"
    # Use lookbehind-ish trimming: sector name must be a short Title Case phrase.
    _SECTOR_RE = re.compile(
        r"(?<![A-Za-z0-9])(?P<sector>[A-Z][A-Za-z][A-Za-z /&\-]{1,60}?)\s+Gross Buy USD\s+"
        r"(?P<buy>-?\d+\.?\d*)\s*mn\s+Gross Sell USD\s+(?P<sell>-?\d+\.?\d*)\s*mn\s+"
        r"Net (?:Buy|Sell) USD\s+(?P<net>-?\d+\.?\d*)\s*mn"
    )

    # Junk prefixes seen in the wild — filter them out of sector names.
    _SECTOR_JUNK_WORDS = {
        "mn", "USD", "Gross", "Buy", "Sell", "Net", "Total",
        "Foriegn", "Foreign", "Sector-wise", "Sectorwise", "Breakup",
    }

    # The only category that represents non-local money. Everything else is
    # domestic flow, which we aggregate as "local".
    _FOREIGN_CATEGORIES = {"foreign", "foreign corporate", "foreign individual"}

    @staticmethod
    def _normalize_category(raw: str) -> str:
        """SCStrade mixes casing ('Foreign' vs 'BANKS / DFI'). Normalize to
        Title Case with tidy spacing so consumers can group/join cleanly."""
        cleaned = re.sub(r"\s+", " ", raw.replace("/", " / ")).strip().lower()
        return " ".join(w.upper() if w in {"dfi", "nbfc"} else w.capitalize()
                        for w in cleaned.split())

    def fetch(self) -> FetchResult:
        """Parse the FIPI text page for participant-category flows + sector flows."""
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=15)
            r.raise_for_status()
            # Strip HTML tags but keep content readable
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text)

            participants: list[dict] = []
            for m in self._PARTICIPANT_RE.finditer(text):
                cat = m.group("cat").strip()
                # Filter out false positives (sector entries mis-caught)
                if "Gross" in cat or "USD" in cat or len(cat) < 3:
                    continue
                participants.append({
                    "category": self._normalize_category(cat),
                    "buy_pkr_mn": float(m.group("buy")),
                    "sell_pkr_mn": float(m.group("sell")),
                    "net_pkr_mn": float(m.group("net")),
                })

            # Aggregate foreign vs local net flows — the single most useful
            # scalar a trading bot needs from this source.
            foreign_net = sum(
                p["net_pkr_mn"] for p in participants
                if p["category"].lower() in self._FOREIGN_CATEGORIES
            )
            local_net = sum(
                p["net_pkr_mn"] for p in participants
                if p["category"].lower() not in self._FOREIGN_CATEGORIES
            )

            sectors: list[dict] = []
            for m in self._SECTOR_RE.finditer(text):
                sector_raw = m.group("sector").strip()
                # Trim leading junk tokens like "mn Foriegn Sector-wise Breakup All other Sectors"
                tokens = sector_raw.split()
                while tokens and tokens[0] in self._SECTOR_JUNK_WORDS:
                    tokens.pop(0)
                sector_clean = " ".join(tokens).strip()
                if not sector_clean:
                    continue
                # A real sector name is usually 1-4 words (e.g. "Cement",
                # "Oil and Gas Exploration", "Banks"). Anything longer is noise.
                if len(tokens) > 5:
                    continue
                sectors.append({
                    "sector": sector_clean,
                    "buy_usd_mn": float(m.group("buy")),
                    "sell_usd_mn": float(m.group("sell")),
                    "net_usd_mn": float(m.group("net")),
                })

            # Extract date stamp if present, e.g. "23-Apr-2026"
            date_match = re.search(r"(\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{4}-\d{2}-\d{2})", text)
            report_date = date_match.group(1) if date_match else None

            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=bool(participants),
                latency_ms=elapsed,
                format="mixed",
                schema=list(participants[0].keys()) if participants else [],
                records=participants,
                extras={
                    "sectors": sectors,
                    "report_date": report_date,
                    "foreign_net_pkr_mn": round(foreign_net, 2),
                    "local_net_pkr_mn": round(local_net, 2),
                },
                summary=(
                    f"{len(participants)} categories, {len(sectors)} sectors, "
                    f"foreign_net={foreign_net:+.1f} mn PKR, "
                    f"local_net={local_net:+.1f} mn PKR, date={report_date}"
                ),
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


# NOTE: FinHisaabFIPIConnector was removed because it was a duplicate of SCStrade
# served via a JS SPA (no structured data from plain HTTP). SCStrade is the
# canonical, scrape-able FIPI/LIPI source.
