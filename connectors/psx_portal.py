"""PSX announcements + circuit breakers (from the PSX data portal)."""

from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from connectors.base import BaseConnector, ConnectionResult, FetchResult
from connectors.sectors import sector_name


def _to_num(s: str) -> float | None:
    if not s:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


# PSX appends suffixes to the SYMBOL on special-status days:
#   XD  = ex-dividend
#   XB  = ex-bonus
#   XR  = ex-rights
#   FUT = futures contract
# The base ticker is unchanged in our universe / OHLCV files, so we
# normalize here once and surface the suffix as a flag so downstream
# consumers (intraday lookups, position monitoring) keep matching.
_PSX_SYMBOL_SUFFIXES = ("XD", "XB", "XR", "XBR")


def _canonical_symbol(raw: str | None) -> tuple[str | None, dict]:
    """Strip PSX corporate-action suffixes from a SYMBOL.

    Returns (canonical_symbol, flags) where flags is a dict like
    {"ex_div": True, "ex_bonus": False, ...}.
    """
    flags = {"ex_div": False, "ex_bonus": False, "ex_rights": False}
    if not raw:
        return raw, flags
    s = raw.strip().upper()
    for suffix in _PSX_SYMBOL_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            base = s[: -len(suffix)]
            if suffix in ("XD",):
                flags["ex_div"] = True
            elif suffix == "XB":
                flags["ex_bonus"] = True
            elif suffix == "XR":
                flags["ex_rights"] = True
            elif suffix == "XBR":
                flags["ex_bonus"] = True
                flags["ex_rights"] = True
            return base, flags
    return s, flags


class PSXCircuitBreakersConnector(BaseConnector):
    name = "PSX Circuit Breakers"
    category = "microstructure"
    layer = "Layer 5 — Microstructure"
    url = "https://dps.psx.com.pk/circuit-breakers"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=12,
                )
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                rows = soup.select("table tr")
                return {
                    "status_code": r.status_code,
                    "rows_found": len(rows),
                    "content_len": len(r.text),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["rows_found"] > 0 or sample["content_len"] > 1000
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"{sample['rows_found']} table rows, {sample['content_len']} bytes",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


    def fetch(self) -> FetchResult:
        """Scrape the upper/lower circuit-locked stock tables.

        Intentionally slim: OHLC / LDCP / change-in-rupees are duplicates of
        the Market Watch snapshot. We only keep what makes this view unique:
        which symbols are circuit-locked, in which direction, at what % and
        with how much volume.
        """
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            tables = soup.find_all("table")

            def parse_table(tbl, direction: str) -> list[dict]:
                rows = tbl.find_all("tr")
                if len(rows) < 2:
                    return []
                headers = [h.get_text(strip=True) for h in rows[0].find_all(["th", "td"])]
                out = []
                for tr in rows[1:]:
                    cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) != len(headers):
                        continue
                    rec = dict(zip(headers, cells))
                    raw_sym = rec.get("SYMBOL")
                    canon, flags = _canonical_symbol(raw_sym)
                    out.append({
                        "symbol": canon,
                        "raw_symbol": raw_sym,
                        "ex_div": flags["ex_div"],
                        "direction": direction,
                        "change_pct": _to_num(rec.get("CHANGE (%)", "")),
                        "volume": _to_num(rec.get("VOLUME", "")),
                    })
                return out

            upper = parse_table(tables[0], "upper") if len(tables) >= 1 else []
            lower = parse_table(tables[1], "lower") if len(tables) >= 2 else []
            combined = upper + lower

            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                format="table",
                schema=["symbol", "direction", "change_pct", "volume"],
                records=combined,
                extras={
                    "upper_locked_count": len(upper),
                    "lower_locked_count": len(lower),
                },
                summary=f"upper_locked={len(upper)}, lower_locked={len(lower)}",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class PSXAnnouncementsConnector(BaseConnector):
    name = "PSX Announcements"
    category = "microstructure-filings"
    layer = "Layer 5 — Microstructure"
    url = "https://dps.psx.com.pk/announcements/companies"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(
                    self.url,
                    headers=self.DEFAULT_HEADERS,
                    timeout=12,
                )
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                items = soup.select(
                    "table tr, .event, .announcement, "
                    "div[data-symbol], .tbl__row, tbody tr"
                )
                return {
                    "status_code": r.status_code,
                    "items_found": len(items),
                    "content_len": len(r.text),
                }

            sample, elapsed = self._timed(pull)
            ok = sample["content_len"] > 1000
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=f"{sample['items_found']} items, {sample['content_len']} bytes",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        """PSX Announcements is a JS SPA — plain HTTP returns no rows. Reach-only."""
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=12)
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name,
                ok=r.ok and len(r.text) > 1000,
                latency_ms=elapsed,
                format="text",
                records=[],
                extras={"status_code": r.status_code, "content_len": len(r.text)},
                summary=(
                    "reachable but JS-rendered — use Playwright or reverse-engineer "
                    "dps.psx.com.pk internal XHR endpoints to get structured filings"
                ),
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class PSXIndicesConnector(BaseConnector):
    """PSX DPS indices page — publishes KSE100, KSE30, KMI30 etc. with OHLC."""

    name = "PSX Indices (DPS)"
    category = "prices-indices"
    layer = "Layer 5 — Microstructure"
    url = "https://dps.psx.com.pk/indices"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=12)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                rows = soup.select("table tr")
                return {"rows": len(rows), "status": r.status_code}

            sample, elapsed = self._timed(pull)
            return ConnectionResult(
                name=self.name, ok=sample["rows"] > 1, latency_ms=elapsed,
                sample=sample, notes=f"{sample['rows']} index rows",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            tables = soup.find_all("table")
            records: list[dict] = []
            for tbl in tables:
                rows = tbl.find_all("tr")
                if len(rows) < 2:
                    continue
                headers = [h.get_text(strip=True) for h in rows[0].find_all(["th", "td"])]
                if "Index" not in headers and "INDEX" not in [h.upper() for h in headers]:
                    continue
                for tr in rows[1:]:
                    cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) != len(headers):
                        continue
                    rec = dict(zip(headers, cells))
                    records.append({
                        "index": rec.get("Index"),
                        "high": _to_num(rec.get("High", "")),
                        "low": _to_num(rec.get("Low", "")),
                        "current": _to_num(rec.get("Current", "")),
                        "change": _to_num(rec.get("Change", "")),
                        "change_pct": _to_num(rec.get("% Change", "")),
                    })
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name, ok=bool(records), latency_ms=elapsed,
                format="table",
                schema=list(records[0].keys()) if records else [],
                records=records,
                summary=f"{len(records)} indices parsed",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )


class PSXMarketWatchConnector(BaseConnector):
    """PSX DPS Market Watch — full snapshot of every listed symbol with OHLCV."""

    name = "PSX Market Watch"
    category = "prices"
    layer = "Layer 5 — Microstructure"
    url = "https://dps.psx.com.pk/market-watch"

    def test(self) -> ConnectionResult:
        try:
            def pull() -> dict:
                r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=15)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                rows = soup.select("table tbody tr")
                return {"rows": len(rows)}

            sample, elapsed = self._timed(pull)
            return ConnectionResult(
                name=self.name, ok=sample["rows"] > 10, latency_ms=elapsed,
                sample=sample, notes=f"{sample['rows']} symbols in market watch",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        start = time.perf_counter()
        try:
            r = requests.get(self.url, headers=self.DEFAULT_HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            tbl = soup.find("table")
            records: list[dict] = []
            if tbl:
                rows = tbl.find_all("tr")
                headers = [h.get_text(strip=True) for h in rows[0].find_all(["th", "td"])]
                for tr in rows[1:]:
                    cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) != len(headers):
                        continue
                    rec = dict(zip(headers, cells))
                    s_code = rec.get("SECTOR")
                    listed_in = rec.get("LISTED IN") or ""
                    indices = [i.strip() for i in listed_in.split(",") if i.strip()]
                    raw_sym = rec.get("SYMBOL")
                    canon, flags = _canonical_symbol(raw_sym)
                    records.append({
                        "symbol": canon,
                        "raw_symbol": raw_sym,
                        "ex_div": flags["ex_div"],
                        "ex_bonus": flags["ex_bonus"],
                        "ex_rights": flags["ex_rights"],
                        "sector_code": s_code,
                        "sector_name": sector_name(s_code),
                        "indices": indices,
                        "ldcp": _to_num(rec.get("LDCP", "")),
                        "open": _to_num(rec.get("OPEN", "")),
                        "high": _to_num(rec.get("HIGH", "")),
                        "low": _to_num(rec.get("LOW", "")),
                        "current": _to_num(rec.get("CURRENT", "")),
                        "change_pct": _to_num(rec.get("CHANGE (%)", "")),
                        "volume": _to_num(rec.get("VOLUME", "")),
                    })
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name, ok=len(records) > 10, latency_ms=elapsed,
                format="table",
                schema=list(records[0].keys()) if records else [],
                records=records,
                summary=f"{len(records)} symbols with OHLCV",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )
