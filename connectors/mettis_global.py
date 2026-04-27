"""Mettis Global news scraper.

Mettis Global (https://mettisglobal.com) is a Pakistani financial news
aggregator. It is uniquely valuable because it republishes every PSX
notice (corporate announcement, board meeting, dividend, material
information) as a navigable news article — so it gives us a
ticker-keyed firehose of corporate actions in addition to general
market news.

This connector is HTML-scrape based (the site does not expose a public
RSS feed). It pulls article titles, dates, summaries, and links from
two listing pages:

    /category/markets/        general PSX / equity coverage
    /category/psx-notices/    PSX corporate announcements (notices)

The output schema mirrors :class:`connectors.rss_news.RssNewsConnector`
so the news scorer pipeline can ingest it without changes.

Design notes
------------
* Graceful degradation. A layout change at mettisglobal.com must NOT
  crash the daily pipeline — every parse step is wrapped in try/except
  and falls back to an empty list with a clear ``error`` message.
* Ticker tagging. Mettis titles often start with ``ABC: announces ...``
  or ``"<Company Name>"``. We map known company names from the
  universe back to PSX symbols as a best-effort hint for downstream
  scoring (the LLM scorer still has the final say).
"""

from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import urljoin

import requests

from connectors.base import BaseConnector, ConnectionResult, FetchResult


_BASE = "https://mettisglobal.news"
_LISTINGS = [
    ("Mettis Global — Home",         f"{_BASE}/"),
    ("Mettis Global — Equity",       f"{_BASE}/Equity"),
    ("Mettis Global — PSX Roundup",  f"{_BASE}/PSXRoundup"),
    ("Mettis Global — Economy",      f"{_BASE}/Economy"),
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", s))).strip()


def _ticker_hits(title: str, summary: str) -> list[str]:
    """Best-effort PSX ticker detection from the title/summary.

    We look for the universe ticker symbol as a whole word AND for the
    company name (case-insensitive). The LLM scorer is the final
    arbiter — this is just a hint for downstream filters.
    """
    try:
        from config.universe import UNIVERSE
    except Exception:
        return []
    text = f" {title} | {summary} "
    text_lower = text.lower()
    hits: list[str] = []
    for ent in UNIVERSE:
        sym = ent.symbol.upper()
        # Whole-word ticker match (avoid e.g. PSO inside SUPSO)
        if re.search(rf"\b{re.escape(sym)}\b", text):
            if sym not in hits:
                hits.append(sym)
            continue
        # Company name match — first 2-3 words is usually distinctive
        # ("Hub Power", "Pakistan Petroleum", "Maple Leaf Cement")
        name_short = " ".join(ent.name.split()[:3]).lower()
        if len(name_short) >= 5 and name_short in text_lower:
            if sym not in hits:
                hits.append(sym)
    return hits


_ARTICLE_HREF_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']*?-\d{4,7})["\'][^>]*>(.*?)</a>',
    flags=re.S | re.I,
)


def _parse_listing(html: str, source_label: str) -> list[dict]:
    """Extract a list of article-meta dicts from one Mettis listing page.

    Mettis Global publishes its actual news at
    ``https://mettisglobal.news``. The listing pages do *not* expose
    article cards via a stable class — instead, each article is
    referenced by an anchor whose href ends with ``-<numeric id>``
    (the post id), and the link text is the headline. The same
    anchor often appears more than once on a page (image link +
    title link); we deduplicate by post id.
    """
    out: list[dict] = []
    seen_ids: set[str] = set()

    for href, inner in _ARTICLE_HREF_RE.findall(html):
        try:
            # Tail of the URL after the last hyphen is the numeric id
            m = re.search(r"-(\d{4,7})\s*$", href)
            if not m:
                continue
            post_id = m.group(1)
            if post_id in seen_ids:
                continue

            # Prefer the first inner heading-like element if the
            # anchor wraps both the headline and a body excerpt.
            head_m = re.search(
                r"<(?:h[1-6]|span|strong|div)[^>]*>(.*?)</(?:h[1-6]|span|strong|div)>",
                inner, flags=re.S | re.I,
            )
            title = _clean(head_m.group(1)) if head_m else _clean(inner)
            if not title or len(title) < 6:
                continue
            title = re.sub(r"\s*Loading\.\.\.\s*", " ", title)
            # Some hero cards put the headline + a long blurb in one
            # anchor without separators — keep just the first sentence
            # if the full string is unreasonably long.
            if len(title) > 180:
                first = re.split(r"(?<=[.!?])\s+", title, maxsplit=1)[0]
                title = first if 12 <= len(first) <= 180 else title[:180]
            title = title.strip()
            if not title:
                continue

            link = href if href.startswith("http") else urljoin(_BASE, href)
            seen_ids.add(post_id)

            tickers = _ticker_hits(title, "")
            out.append({
                "source":       source_label,
                "title":        title[:240],
                "published_at": None,   # listing pages don't expose dates
                "link":         link,
                "summary":      "",
                "ticker_hits":  ",".join(tickers),
            })
        except Exception:
            continue
    return out


class MettisGlobalConnector(BaseConnector):
    """Scrape recent articles from mettisglobal.com listing pages."""

    name = "Mettis Global News"
    category = "news"
    layer = "Layer 4 — Behavioral / Layer 8 — Corporate"
    url = _BASE

    TIMEOUT = 15

    def _get(self, url: str) -> str:
        r = requests.get(
            url,
            headers=self.DEFAULT_HEADERS,
            timeout=self.TIMEOUT,
        )
        r.raise_for_status()
        return r.text

    def test(self) -> ConnectionResult:
        try:
            def run() -> dict:
                ok_pages = 0
                first_titles: list[str] = []
                for label, url in _LISTINGS:
                    try:
                        html = self._get(url)
                        items = _parse_listing(html, label)
                        if items:
                            ok_pages += 1
                            first_titles.append(items[0]["title"][:60])
                    except Exception:
                        continue
                return {"ok_pages": ok_pages,
                         "first_titles": first_titles}

            sample, elapsed = self._timed(run)
            ok = sample["ok_pages"] >= 1
            notes = (f"{sample['ok_pages']}/{len(_LISTINGS)} listings parsed; "
                     f"sample={sample['first_titles']}")
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=notes,
                error=None if ok else "no listings returned articles",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self, per_listing: int = 12) -> FetchResult:
        """Pull the most recent articles from each Mettis listing page.

        Returns a flattened list, newest-first when timestamps parse,
        otherwise listing-then-page order.
        """
        start = time.perf_counter()
        records: list[dict] = []
        per_source: dict[str, int] = {}
        errors: list[str] = []

        for label, url in _LISTINGS:
            try:
                html = self._get(url)
                items = _parse_listing(html, label)[: per_listing]
                per_source[label] = len(items)
                records.extend(items)
            except Exception as ex:
                errors.append(f"{label}: {type(ex).__name__}: {ex}")
                per_source[label] = 0

        # Sort newest-first when published_at parses
        records.sort(
            key=lambda r: r.get("published_at") or "",
            reverse=True,
        )

        elapsed = (time.perf_counter() - start) * 1000.0
        return FetchResult(
            name=self.name,
            ok=bool(records),
            latency_ms=elapsed,
            format="table",
            schema=["source", "title", "published_at", "link",
                    "summary", "ticker_hits"],
            records=records,
            extras={"per_source": per_source, "errors": errors},
            summary=f"{len(records)} articles from {len(per_source)} listings",
        )


if __name__ == "__main__":  # pragma: no cover  (manual run)
    c = MettisGlobalConnector()
    pr = c.test()
    print(f"test: ok={pr.ok}  latency={pr.latency_ms:.0f}ms  notes={pr.notes}")
    if pr.ok:
        fr = c.fetch(per_listing=8)
        print(f"\nfetch: {fr.summary}  ({fr.latency_ms:.0f}ms)")
        for r in fr.records[:10]:
            t = (r["title"] or "")[:80]
            tags = r.get("ticker_hits") or "—"
            print(f"  [{r.get('published_at') or '—':<25}] {t:<80}  "
                  f"tickers={tags}")
