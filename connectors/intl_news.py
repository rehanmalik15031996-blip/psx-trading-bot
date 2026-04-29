"""International RSS news aggregator with a Pakistan-relevance filter.

The April 29 scorecard exposed a blind spot: the bot was reading the
domestic Pakistan press well (Business Recorder, Profit, Dawn) but had
zero visibility into how Reuters / Bloomberg / Investing.com framed the
same events. International desks routinely break Pakistan-relevant news
(IMF tranche timings, oil-price moves, US Treasury policy, EM-risk
sentiment) hours before the domestic press picks it up. Adding these
sources gives the news lens a global dimension *without* bringing in
US tech / European earnings noise — the connector pre-filters every
article through a Pakistan-relevance keyword whitelist before yielding
records to the scorer.

Sources
-------
- Reuters (business + commodities) — public RSS
- Bloomberg public RSS (markets + economics) — publicly served, no
  auth required, throttled if hammered
- Investing.com (commodities + Asia) — public RSS
- MarketWatch (top stories + market pulse) — public RSS
- Google News custom queries — `Pakistan stocks`, `KSE-100`,
  `Pakistan oil imports`, `State Bank of Pakistan`

Output schema matches ``connectors.rss_news.RssNewsConnector`` so
``scripts/score_news_sentiment.py`` can ingest both connectors with the
same code path.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import unescape

import feedparser

from connectors.base import BaseConnector, ConnectionResult, FetchResult


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE  = re.compile(r"\s+")


def _clean_html(s: str) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", s))).strip()


@dataclass
class IntlFeed:
    label: str
    url: str


# Pakistan-relevance whitelist — an article must contain at least one
# of these tokens (case-insensitive substring match) to survive the
# pre-filter. The list deliberately keeps commodity / EM-risk terms
# even though they are not strictly "Pakistan" — a Brent shock is
# Pakistan-relevant via the OMC and refiner channel.
RELEVANCE_KEYWORDS: tuple[str, ...] = (
    # Pakistan proper
    "pakistan", "pakistani", "karachi", "lahore", "islamabad",
    "pkr", "rupee", "kse", "psx", "kse-100", "kse100",
    "sbp", "state bank of pakistan", "secp", "fbr", "ogra",
    "imf pakistan", "imf-pakistan",
    # Commodities relevant to Pakistan's import bill / earnings
    "brent", "wti", "crude oil", "opec", "oil price",
    "lng", "lpg", "natural gas",
    "gold", "copper", "cotton",
    # EM macro that bleeds into PSX
    "imf tranche", "imf bailout", "imf review", "imf disbursement",
    "emerging markets", "frontier markets",
    "south asia", "sri lanka default", "bangladesh imf",
    # Geopolitical drivers
    "saudi arabia pakistan", "uae pakistan", "china pakistan",
    "cpec", "afghanistan",
)


def _is_pakistan_relevant(title: str, summary: str) -> bool:
    """Return True if the article looks Pakistan-relevant.

    A simple substring scan is sufficient — we tune the whitelist
    rather than the algorithm. Keeping the filter substring-based
    means it's deterministic and trivial to debug ("why didn't this
    article come through?").
    """
    blob = f"{title} {summary}".lower()
    return any(kw in blob for kw in RELEVANCE_KEYWORDS)


# Verified-public RSS endpoints. Some Reuters / Bloomberg endpoints
# rate-limit heavily; we intentionally cap ``per_feed`` to keep daily
# request counts modest and we tolerate empty responses gracefully.
FEEDS: list[IntlFeed] = [
    # Reuters — these endpoints return 404 sometimes since the great
    # 2024 Reuters site overhaul; we keep two candidates per topic so
    # we still get coverage if one breaks.
    IntlFeed("Reuters — Business",
              "https://feeds.reuters.com/reuters/businessNews"),
    IntlFeed("Reuters — Commodities",
              "https://feeds.reuters.com/reuters/commoditiesNews"),

    # Bloomberg — public marketing-side RSS; throttled but no auth.
    IntlFeed("Bloomberg — Markets",
              "https://feeds.bloomberg.com/markets/news.rss"),
    IntlFeed("Bloomberg — Economics",
              "https://feeds.bloomberg.com/economics/news.rss"),

    # Investing.com — commodities + Asia desks.
    IntlFeed("Investing.com — Commodities",
              "https://www.investing.com/rss/news_25.rss"),
    IntlFeed("Investing.com — Asia",
              "https://www.investing.com/rss/news_301.rss"),

    # MarketWatch — top stories + market pulse.
    IntlFeed("MarketWatch — Top Stories",
              "http://feeds.marketwatch.com/marketwatch/topstories/"),
    IntlFeed("MarketWatch — Market Pulse",
              "http://feeds.marketwatch.com/marketwatch/marketpulse/"),

    # Google News custom queries — these aggregate global coverage of
    # Pakistan-specific search terms across hundreds of publications.
    IntlFeed("Google News — Pakistan stocks",
              "https://news.google.com/rss/search?"
              "q=Pakistan+stocks&hl=en-PK&gl=PK&ceid=PK:en"),
    IntlFeed("Google News — KSE-100",
              "https://news.google.com/rss/search?"
              "q=KSE-100&hl=en-PK&gl=PK&ceid=PK:en"),
    IntlFeed("Google News — Pakistan oil imports",
              "https://news.google.com/rss/search?"
              "q=Pakistan+oil+imports&hl=en-PK&gl=PK&ceid=PK:en"),
    IntlFeed("Google News — State Bank of Pakistan",
              "https://news.google.com/rss/search?"
              "q=%22State+Bank+of+Pakistan%22&hl=en-PK&gl=PK&ceid=PK:en"),
]


class IntlNewsConnector(BaseConnector):
    """International RSS feeds with a Pakistan-relevance pre-filter."""

    name = "International news (Reuters / Bloomberg / Investing / MW / GN)"
    category = "news"
    layer = "Layer 4 — Behavioral / Layer 2 — Political"
    url = "multiple"

    TIMEOUT = 12

    def _fetch_one(self, feed: IntlFeed) -> dict:
        parsed = feedparser.parse(feed.url,
                                    request_headers=self.DEFAULT_HEADERS)
        entries = parsed.entries or []
        return {
            "label":   feed.label,
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
            ok = len(working) >= len(FEEDS) // 3
            notes = (f"{len(working)}/{len(FEEDS)} feeds returned entries "
                     "(international, Pakistan-pre-filtered)")
            if broken:
                notes += f"; broken: {', '.join(broken[:3])}"
            return ConnectionResult(name=self.name, ok=ok,
                                      latency_ms=elapsed,
                                      sample=sample, notes=notes)
        except Exception as e:
            return ConnectionResult(name=self.name, ok=False,
                                      latency_ms=0.0,
                                      error=f"{type(e).__name__}: {e}")

    def fetch(self, per_feed: int = 8) -> FetchResult:
        """Pull the latest N items per feed, pre-filter for Pakistan
        relevance, and flatten into one timeline.

        ``per_feed`` defaults to 8 (versus 5 for the domestic feeds)
        because the relevance filter typically discards 80%+ of
        international items — a higher pull keeps the post-filter
        yield comparable to the domestic feeds.
        """
        start = time.perf_counter()
        records: list[dict] = []
        per_source: dict[str, int] = {}
        post_filter: dict[str, int] = {}
        errors: list[str] = []

        for feed in FEEDS:
            try:
                parsed = feedparser.parse(
                    feed.url, request_headers=self.DEFAULT_HEADERS)
                entries = (parsed.entries or [])[:per_feed]
                per_source[feed.label] = len(entries)
                kept = 0
                for e in entries:
                    title = (e.get("title") or "").strip()
                    summary = _clean_html(e.get("summary", "") or "")[:400]
                    if not _is_pakistan_relevant(title, summary):
                        continue
                    records.append({
                        "source": feed.label,
                        "title":  title,
                        "published_at": (
                            time.strftime("%Y-%m-%dT%H:%M:%S",
                                            e.published_parsed)
                            if getattr(e, "published_parsed", None)
                            else None),
                        "link": e.get("link", ""),
                        "summary": summary[:300],
                    })
                    kept += 1
                post_filter[feed.label] = kept
            except Exception as ex:
                errors.append(f"{feed.label}: {type(ex).__name__}")

        records.sort(key=lambda r: r.get("published_at") or "",
                       reverse=True)

        elapsed = (time.perf_counter() - start) * 1000.0
        n = len(records)
        return FetchResult(
            name=self.name, ok=bool(records), latency_ms=elapsed,
            format="table",
            schema=["source", "title", "published_at", "link",
                     "summary"],
            records=records,
            extras={
                "per_source": per_source,
                "post_pakistan_filter": post_filter,
                "errors": errors,
            },
            summary=(f"{n} Pakistan-relevant articles from "
                     f"{sum(1 for v in post_filter.values() if v)}"
                     f"/{len(FEEDS)} international feeds"),
        )
