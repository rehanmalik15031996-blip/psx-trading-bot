"""RSS news aggregator — Pakistan financial + geopolitical feeds.

Sources (free, public RSS):
- Business Recorder
- Profit by Pakistan Today
- Dawn business
- The News business
- Tribune business
- IMF press releases
- SBP RSS
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import unescape

import feedparser

from connectors.base import BaseConnector, ConnectionResult, FetchResult


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html(s: str) -> str:
    """Strip tags/entities from RSS summaries so downstream NLP doesn't have
    to deal with markup. Keeps about the same length budget."""
    if not s:
        return ""
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", s))).strip()


@dataclass
class RssFeed:
    label: str
    url: str


# Verified-working RSS feeds as of April 2026.
# Notes:
# - IMF and SBP RSS endpoints return 403 to programmatic requests. Use HTML
#   scraping of https://www.imf.org/en/Countries/PAK and https://www.sbp.org.pk/
#   as fallbacks (implemented via IMFPakistanConnector + SBP connectors).
# - Business Recorder /feeds/latest also 403s; /feeds/markets works reliably.
FEEDS: list[RssFeed] = [
    RssFeed("Business Recorder — Markets", "https://www.brecorder.com/feeds/markets"),
    RssFeed("Profit by Pakistan Today", "https://profit.pakistantoday.com.pk/feed/"),
    RssFeed("Dawn Business", "https://www.dawn.com/feeds/business"),
    RssFeed("The News — Business", "https://www.thenews.com.pk/rss/1/3"),
    RssFeed("Tribune Business", "https://tribune.com.pk/feed/business"),
]


class RssNewsConnector(BaseConnector):
    name = "RSS News Aggregator"
    category = "news"
    layer = "Layer 4 — Behavioral / Layer 2 — Political"
    url = "multiple"

    TIMEOUT = 12

    def _fetch_one(self, feed: RssFeed) -> dict:
        parsed = feedparser.parse(feed.url, request_headers=self.DEFAULT_HEADERS)
        entries = parsed.entries or []
        return {
            "label": feed.label,
            "entries": len(entries),
            "latest_title": entries[0].get("title", "") if entries else "",
        }

    def test(self) -> ConnectionResult:
        def run() -> list[dict]:
            return [self._fetch_one(f) for f in FEEDS]

        try:
            results, elapsed = self._timed(run)
            working = [r for r in results if r["entries"] > 0]
            broken = [r["label"] for r in results if r["entries"] == 0]
            sample = {r["label"]: r["entries"] for r in results}
            ok = len(working) >= len(FEEDS) // 2
            notes = f"{len(working)}/{len(FEEDS)} feeds returned entries"
            if broken:
                notes += f"; broken: {', '.join(broken[:3])}"
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=notes,
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self, per_feed: int = 5) -> FetchResult:
        """Pull the latest N items from each feed, flattened into one timeline."""
        start = time.perf_counter()
        records: list[dict] = []
        per_source: dict[str, int] = {}
        errors: list[str] = []

        for feed in FEEDS:
            try:
                parsed = feedparser.parse(feed.url, request_headers=self.DEFAULT_HEADERS)
                entries = (parsed.entries or [])[:per_feed]
                per_source[feed.label] = len(entries)
                for e in entries:
                    records.append({
                        "source": feed.label,
                        "title": e.get("title", "").strip(),
                        "published_at": (
                            time.strftime("%Y-%m-%dT%H:%M:%S", e.published_parsed)
                            if getattr(e, "published_parsed", None) else None
                        ),
                        "link": e.get("link", ""),
                        "summary": _clean_html(e.get("summary", "") or "")[:300],
                    })
            except Exception as ex:
                errors.append(f"{feed.label}: {type(ex).__name__}")

        # Sort by parsed date descending where available
        records.sort(key=lambda r: r.get("published_at") or "", reverse=True)

        elapsed = (time.perf_counter() - start) * 1000.0
        return FetchResult(
            name=self.name, ok=bool(records), latency_ms=elapsed,
            format="table",
            schema=["source", "title", "published_at", "link", "summary"],
            records=records,
            extras={"per_source": per_source, "errors": errors},
            summary=f"{len(records)} articles from {len(per_source)} feeds",
        )
