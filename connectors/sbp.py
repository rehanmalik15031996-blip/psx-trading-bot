"""SBP connectors — Policy Rate, M2M rate (PKR/USD), EasyData portal reachability."""

from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectionResult, FetchResult


def _f(s: str) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _iso_date(raw: str | None) -> str | None:
    """Convert SBP's '23-Apr-26' style to ISO '2026-04-23'. None-safe."""
    if not raw:
        return None
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2,4})", raw.strip())
    if not m:
        return raw
    day, mon, yr = m.group(1), m.group(2).lower(), m.group(3)
    if mon not in _MONTHS:
        return raw
    year = int(yr)
    if year < 100:
        year += 2000
    return f"{year:04d}-{_MONTHS[mon]}-{int(day):02d}"


def _pull_sbp_dashboard() -> tuple[str, int]:
    """Fetch the SBP M2M dashboard — this one page has policy rate, KIBOR,
    T-Bill/PIB yields, reserves, and PKR/USD in one go."""
    r = requests.get(
        "https://www.sbp.org.pk/ecodata/rates/m2m/M2M-Current.asp",
        headers={"User-Agent": "Mozilla/5.0 PSX-Bot"},
        timeout=15,
    )
    r.raise_for_status()
    return r.text, r.status_code


class SBPPolicyRateConnector(BaseConnector):
    name = "SBP Policy Rate + KIBOR"
    category = "macro-rates"
    layer = "Layer 1 — Macro"
    url = "https://www.sbp.org.pk/ecodata/rates/m2m/M2M-Current.asp"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                html, sc = _pull_sbp_dashboard()
                text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))
                m = re.search(r"SBP\s*Policy\s*Rat\s*e?\s*([0-9]+\.[0-9]+)%", text, re.I)
                rate = float(m.group(1)) if m else None
                return {"status_code": sc, "latest_rate_pct": rate, "content_len": len(html)}

            sample, elapsed = self._timed(pull)
            ok = sample["status_code"] == 200 and sample["latest_rate_pct"] is not None
            return ConnectionResult(
                name=self.name, ok=ok, latency_ms=elapsed, sample=sample,
                notes=(
                    f"Policy rate = {sample['latest_rate_pct']}% (scraped)"
                    if sample["latest_rate_pct"] else "Page reachable"
                ),
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        """Extract policy rate corridor, KIBOR, T-Bill / PIB / GIS yields, reserves."""
        start = time.perf_counter()
        try:
            html, _ = _pull_sbp_dashboard()
            text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))

            def grab(pattern: str) -> float | None:
                m = re.search(pattern, text, re.I)
                return float(m.group(1)) if m else None

            policy_rate = grab(r"SBP\s*Policy\s*Rat\s*e?\s*([0-9]+\.[0-9]+)\s*%")
            ceiling = grab(r"Overnight\s*Reverse\s*Repo\s*\(Ceiling\)\s*Rate?\s*([0-9]+\.[0-9]+)\s*%")
            floor = grab(r"Overnight\s*Repo\s*\(Floor\)\s*Rate\s*([0-9]+\.[0-9]+)\s*%")
            weighted_on_repo = grab(r"Weighted-average\s*Overnight\s*Repo\s*Rate\s*As on\s*[\d\-A-Za-z]+\s*([0-9]+\.[0-9]+)\s*%")

            # KIBOR: Tenor BID OFFER 3-M X Y 6-M X Y 12-M X Y
            kibor = {}
            for tenor in ("3-M", "6-M", "12-M"):
                m = re.search(rf"{tenor}\s+([0-9]+\.[0-9]+)\s+([0-9]+\.[0-9]+)", text)
                if m:
                    kibor[tenor] = {"bid": float(m.group(1)), "offer": float(m.group(2))}

            # MTB (T-Bill) cut-off yields. The table layout is:
            #   Tenor Cut-off Yield 1-M 10.6982% 3-M 11.4380% 6-M 11.1549% 12-M 11.8900%
            mtb: dict[str, float] = {}
            mtb_block_match = re.search(
                r"Tenor\s+Cut-off\s+Yield\s+([\s\S]{0,400}?)(?:\(as|Tenor\s+Cut-off\s+Rates|PIB)",
                text, re.I,
            )
            mtb_blob = mtb_block_match.group(1) if mtb_block_match else text
            for m in re.finditer(r"(1-M|3-M|6-M|12-M)\s+([0-9]+\.[0-9]+)\s*%", mtb_blob):
                mtb.setdefault(m.group(1), float(m.group(2)))

            # PIB cut-off rates (fixed-rate): 2-Y, 3-Y, 5-Y, 10-Y, 15-Y
            pib: dict[str, float | str] = {}
            pib_block_match = re.search(
                r"Tenor\s+Cut-off\s+Rates\s+([\s\S]{0,500}?)(?:\(as|Tenor\s+Cut-off\s+Price|GIS)",
                text, re.I,
            )
            if pib_block_match:
                blob = pib_block_match.group(1)
                for m in re.finditer(
                    r"(2-Y|3-Y|5-Y|10-Y|15-Y|20-Y|30-Y)\s+(?:([0-9]+\.[0-9]+)\s*%|(Bids Rejected))",
                    blob,
                ):
                    pib[m.group(1)] = float(m.group(2)) if m.group(2) else m.group(3)

            # Reserves (USD mn) — SBP, Banks, Total
            reserves = {}
            m = re.search(
                r"SBP.{0,5}s\s*Reserves?\s*([0-9,]+\.[0-9]+)\s*Bank.{0,5}s\s*Reserves?\s*([0-9,]+\.[0-9]+)\s*Total\s*Reserves\s*([0-9,]+\.[0-9]+)",
                text,
            )
            if m:
                reserves = {
                    "sbp_usd_mn": _f(m.group(1)),
                    "banks_usd_mn": _f(m.group(2)),
                    "total_usd_mn": _f(m.group(3)),
                }

            # Date
            m = re.search(r"As\s*on\s*(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s*M2M", text)
            as_on = _iso_date(m.group(1)) if m else None

            # Coerce "Bids Rejected" and similar strings to None so the
            # pib_yields_pct map stays typed for downstream ML usage.
            pib_numeric = {
                k: (v if isinstance(v, (int, float)) else None)
                for k, v in pib.items()
            }

            record = {
                "as_on": as_on,
                "policy_rate_pct": policy_rate,
                "ceiling_rate_pct": ceiling,
                "floor_rate_pct": floor,
                "weighted_on_repo_pct": weighted_on_repo,
                "kibor": kibor,
                "tbill_yields_pct": mtb,
                "pib_yields_pct": pib_numeric,
                "reserves_usd_mn": reserves,
            }

            elapsed = (time.perf_counter() - start) * 1000.0
            ok = policy_rate is not None
            return FetchResult(
                name=self.name, ok=ok, latency_ms=elapsed,
                format="json",
                schema=list(record.keys()),
                records=[record],
                summary=(
                    f"policy={policy_rate}% corridor={floor}-{ceiling}%, "
                    f"KIBOR={len(kibor)} tenors, T-Bill={len(mtb)} tenors, "
                    f"PIB={len(pib)} tenors, as_on={as_on}"
                ),
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class SBPMarkToMarketConnector(BaseConnector):
    name = "SBP M2M (PKR/USD)"
    category = "macro-fx"
    layer = "Layer 1 — Macro"
    url = "https://www.sbp.org.pk/ecodata/rates/m2m/M2M-Current.asp"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                html, sc = _pull_sbp_dashboard()
                text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))
                m = re.search(r"M2M\s*Revaluation\s*Rate\s*([0-9]+\.[0-9]+)", text)
                rate = float(m.group(1)) if m else None
                return {"status_code": sc, "m2m_rate": rate, "content_len": len(html)}

            sample, elapsed = self._timed(pull)
            ok = sample["m2m_rate"] is not None
            return ConnectionResult(
                name=self.name, ok=ok, latency_ms=elapsed, sample=sample,
                notes=f"PKR/USD M2M = {sample['m2m_rate']}" if ok else "rate not parsed",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        start = time.perf_counter()
        try:
            html, _ = _pull_sbp_dashboard()
            text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" "))

            m2m = re.search(r"M2M\s*Revaluation\s*Rate\s*([0-9]+\.[0-9]+)", text)
            wa = re.search(r"Weighted\s*Average\s*Rate\s*Bid:\s*([0-9]+\.[0-9]+)\s*Offer:\s*([0-9]+\.[0-9]+)", text)
            as_on_m = re.search(r"As\s*on\s*(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s*M2M", text)

            bid = float(wa.group(1)) if wa else None
            offer = float(wa.group(2)) if wa else None
            rec = {
                "as_on": _iso_date(as_on_m.group(1)) if as_on_m else None,
                "m2m_rate": float(m2m.group(1)) if m2m else None,
                "weighted_avg_bid": bid,
                "weighted_avg_offer": offer,
                "spread_pkr": round(offer - bid, 4) if (bid is not None and offer is not None) else None,
            }
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name, ok=rec["m2m_rate"] is not None,
                latency_ms=elapsed, format="json",
                schema=list(rec.keys()), records=[rec],
                summary=f"PKR/USD M2M={rec['m2m_rate']} bid/offer={rec['weighted_avg_bid']}/{rec['weighted_avg_offer']} spread={rec['spread_pkr']} as_on={rec['as_on']}",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class SBPEasyDataConnector(BaseConnector):
    """Reachability check for SBP EasyData portal.

    Full API access typically requires a free API key (register on the portal).
    """

    name = "SBP EasyData (portal reach)"
    category = "macro-everything"
    layer = "Layer 1 — Macro"
    url = "https://easydata.sbp.org.pk/"

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
                notes=f"HTTP {sample['status_code']} — API key needed for programmatic access",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        """EasyData is a portal: programmatic access needs a free API key
        (register at https://easydata.sbp.org.pk). Reach-only health check."""
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=12)
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name, ok=r.ok, latency_ms=elapsed,
                format="text", records=[],
                extras={"status_code": r.status_code, "content_len": len(r.text)},
                summary=(
                    "reachable — register for free API key on portal for "
                    "programmatic series access (M2, CPI, reserves, FX)"
                ),
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )
