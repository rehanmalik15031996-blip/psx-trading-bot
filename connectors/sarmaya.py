"""Sarmaya.com fundamentals cross-check.

Sarmaya (https://sarmaya.com) is a Pakistani retail investing portal
that publishes a clean per-symbol fundamentals snapshot — P/E, P/B,
EPS, dividend yield, market cap, and a sector P/E reference. We use
it as a sanity check against the yfinance feed so that any
material disagreement (>25%) flags a data-quality warning the
analyst can investigate.

Cache layout::

    data/fundamentals/_sarmaya/{SYMBOL}.json     # one snapshot per symbol

Refreshed weekly (cheap — only ~15 symbols).

Public API
----------
    SarmayaConnector().fetch_one(symbol="OGDC") -> dict
    SarmayaConnector().fetch(symbols=None)      -> FetchResult
    crosscheck(symbol)                          -> dict
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus

import requests

from connectors.base import BaseConnector, ConnectionResult, FetchResult


_BASE = "https://sarmaya.com"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "fundamentals" / "_sarmaya"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"-?[\d,]+\.?\d*")


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", s))).strip()


def _to_float(s: str | None) -> float | None:
    if not s:
        return None
    m = _NUM_RE.search(str(s).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _candidate_urls(symbol: str) -> list[str]:
    """URL patterns Sarmaya has used over time. We try them in order
    until one returns a 200 with parseable content, so a future site
    redesign tends to keep working."""
    sym = symbol.upper().strip()
    return [
        f"{_BASE}/symbol/{sym}",
        f"{_BASE}/symbol/{sym.lower()}",
        f"{_BASE}/{sym}",
        f"{_BASE}/?s={quote_plus(sym)}",
    ]


_LABEL_KEYS = {
    # Map sarmaya display label (lowercased, stripped) -> our schema key
    "p/e ratio":           "pe_ratio",
    "p/e":                 "pe_ratio",
    "price to earnings":   "pe_ratio",
    "p/b ratio":           "pb_ratio",
    "p/b":                 "pb_ratio",
    "price to book":       "pb_ratio",
    "eps":                 "eps_ttm",
    "earnings per share":  "eps_ttm",
    "bvps":                "book_value_per_share",
    "book value per share": "book_value_per_share",
    "dividend yield":      "dividend_yield_pct",
    "dy":                  "dividend_yield_pct",
    "payout ratio":        "payout_ratio_pct",
    "market cap":          "market_cap_pkr_mn",
    "market capitalization": "market_cap_pkr_mn",
    "sector p/e":          "sector_pe_ratio",
    "sector pe":           "sector_pe_ratio",
}


def _parse_fundamentals(html: str) -> dict:
    """Extract numeric fundamentals from a Sarmaya symbol page.

    Sarmaya renders fundamentals as a series of label-value pairs
    inside a stats grid. We extract them generically by scanning each
    short text node and matching against ``_LABEL_KEYS`` — that way a
    minor markup change tends to keep working as long as the labels
    are recognisable.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return {"error": "beautifulsoup4 not installed"}

    out: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: definition list / stat-card layout (most common).
    # Look for short text nodes that match a known label, then take
    # the immediately following sibling that has a number.
    for el in soup.find_all(string=True):
        text = _clean(str(el)).lower().rstrip(":")
        if not text or len(text) > 40:
            continue
        if text not in _LABEL_KEYS:
            continue
        key = _LABEL_KEYS[text]
        if key in out:
            continue
        # Find the next sibling element with text containing a number
        parent = el.parent
        candidates = []
        if parent is not None:
            sib = parent
            for _ in range(4):
                sib = sib.find_next() if sib is not None else None
                if sib is None:
                    break
                txt = _clean(sib.get_text() if hasattr(sib, "get_text") else str(sib))
                if not txt:
                    continue
                if _NUM_RE.search(txt.replace(",", "")):
                    candidates.append(txt)
                if len(candidates) >= 2:
                    break
        if candidates:
            val = _to_float(candidates[0])
            if val is not None:
                out[key] = val

    # Strategy 2: simple regex fallback on full text.
    if not out:
        full = _clean(soup.get_text())
        for label, key in _LABEL_KEYS.items():
            if key in out:
                continue
            m = re.search(
                rf"{re.escape(label)}\s*[:\-]?\s*({_NUM_RE.pattern})",
                full,
                flags=re.IGNORECASE,
            )
            if m:
                v = _to_float(m.group(1))
                if v is not None:
                    out[key] = v

    return out


class SarmayaConnector(BaseConnector):
    """Per-symbol fundamentals snapshot from sarmaya.com."""

    name = "Sarmaya.com fundamentals"
    category = "fundamentals"
    layer = "Layer 4 — Fundamentals (cross-check)"
    url = _BASE

    TIMEOUT = 15
    PROBE_SYMBOL = "OGDC"

    def _get(self, url: str) -> str | None:
        try:
            r = requests.get(
                url,
                headers=self.DEFAULT_HEADERS,
                timeout=self.TIMEOUT,
                allow_redirects=True,
            )
            if r.status_code != 200:
                return None
            return r.text
        except Exception:
            return None

    def fetch_one(self, symbol: str) -> dict:
        """Try each candidate URL until one parses; return whichever
        snapshot has the most fields populated."""
        rec: dict = {
            "symbol": symbol.upper(),
            "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ok": False,
            "source_url": None,
        }
        best: dict = {}
        for url in _candidate_urls(symbol):
            html = self._get(url)
            if not html:
                continue
            parsed = _parse_fundamentals(html)
            if not parsed or "error" in parsed:
                continue
            if len(parsed) > len(best):
                best = parsed
                rec["source_url"] = url
            if len(best) >= 4:  # good enough — stop trying alternates
                break
        if best:
            rec["ok"] = True
            rec.update(best)
        else:
            rec["error"] = "no parseable Sarmaya page"
        return rec

    def fetch(self, symbols: list[str] | None = None) -> FetchResult:
        from config.universe import symbols as universe_symbols

        syms = symbols or universe_symbols()
        start = time.perf_counter()
        records: list[dict] = []
        ok_count = 0
        for sym in syms:
            r = self.fetch_one(sym)
            records.append(r)
            if r.get("ok"):
                ok_count += 1
            try:
                (CACHE_DIR / f"{sym.upper()}.json").write_text(
                    json.dumps(r, indent=2, default=str), encoding="utf-8")
            except Exception:
                pass

        elapsed = (time.perf_counter() - start) * 1000.0
        return FetchResult(
            name=self.name,
            ok=ok_count > 0,
            latency_ms=elapsed,
            format="json",
            schema=["symbol", "pe_ratio", "pb_ratio", "eps_ttm",
                    "dividend_yield_pct", "market_cap_pkr_mn"],
            records=records,
            extras={"per_symbol": {r["symbol"]: r.get("ok", False)
                                    for r in records}},
            summary=f"{ok_count}/{len(syms)} symbols parsed",
        )

    def test(self) -> ConnectionResult:
        try:
            sample, elapsed = self._timed(self.fetch_one, self.PROBE_SYMBOL)
            ok = bool(sample.get("ok"))
            populated = [k for k in
                          ("pe_ratio", "pb_ratio", "eps_ttm",
                           "dividend_yield_pct")
                          if k in sample]
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample={k: sample.get(k) for k in populated},
                notes=(f"probe {self.PROBE_SYMBOL}: "
                       f"{len(populated)} fields populated"),
                error=None if ok else (sample.get("error")
                                          or "unable to parse Sarmaya page"),
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )


# ----------------------------------------------------------------- helpers
def load_cached(symbol: str) -> dict | None:
    """Read the most recent Sarmaya snapshot for ``symbol``."""
    p = CACHE_DIR / f"{symbol.upper()}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def crosscheck(symbol: str, tolerance_pct: float = 25.0) -> dict:
    """Compare Sarmaya vs yfinance for one symbol.

    Returns a dict with ``flags`` listing every field that disagrees
    by more than ``tolerance_pct``. yfinance is treated as the
    authoritative source; this only surfaces a warning to the
    analyst, it does NOT overwrite anything.
    """
    from connectors.yfinance_fundamentals import load_latest as _yf

    yf_rec = _yf(symbol) or {}
    sm_rec = load_cached(symbol) or {}
    pairs = [
        ("pe_ratio", "pe_ratio"),
        ("pb_ratio", "pb_ratio"),
        ("dividend_yield_pct", "dividend_yield_pct"),
        ("eps_ttm", "eps_ttm"),
        ("book_value_per_share", "book_value_per_share"),
    ]
    flags: list[dict] = []
    for yf_k, sm_k in pairs:
        yf_v = yf_rec.get(yf_k)
        sm_v = sm_rec.get(sm_k)
        if yf_v in (None, 0) or sm_v in (None, 0):
            continue
        try:
            yf_v_f = float(yf_v)
            sm_v_f = float(sm_v)
        except (TypeError, ValueError):
            continue
        if yf_v_f == 0:
            continue
        diff_pct = abs(sm_v_f - yf_v_f) / abs(yf_v_f) * 100.0
        if diff_pct > tolerance_pct:
            flags.append({
                "field": yf_k,
                "yfinance": round(yf_v_f, 4),
                "sarmaya": round(sm_v_f, 4),
                "diff_pct": round(diff_pct, 1),
            })
    return {
        "symbol": symbol.upper(),
        "yfinance_present": bool(yf_rec),
        "sarmaya_present": bool(sm_rec),
        "tolerance_pct": tolerance_pct,
        "n_flags": len(flags),
        "flags": flags,
        "sarmaya_source_url": sm_rec.get("source_url"),
        "sarmaya_as_of_utc": sm_rec.get("as_of_utc"),
    }


if __name__ == "__main__":  # pragma: no cover  (manual run)
    c = SarmayaConnector()
    pr = c.test()
    print(f"test: ok={pr.ok}  latency={pr.latency_ms:.0f}ms  "
           f"notes={pr.notes}")
    if pr.error:
        print(f"  error: {pr.error}")
