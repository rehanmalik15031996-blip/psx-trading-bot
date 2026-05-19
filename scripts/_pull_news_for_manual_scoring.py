"""Pull fresh RSS articles (no LLM) and dump them as JSON so the
agent can score them by hand. Replaces the Anthropic call in
scripts/score_news_sentiment.py for days when the API key is bad.

Outputs:
  data/news/_pending_articles.json   list of unscored articles
  prints a summary count by source

After agent scores -> use scripts/_apply_manual_news_scores.py to
write them back into data/news/scored_news.parquet.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from connectors.rss_news import RssNewsConnector
from connectors.mettis_global import MettisGlobalConnector
from connectors.intl_news import IntlNewsConnector
from config.universe import symbols as universe_symbols

CACHE_DIR = ROOT / "data" / "news"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = CACHE_DIR / "scored_news.parquet"
PENDING = CACHE_DIR / "_pending_articles.json"

UNIVERSE = set(universe_symbols())


def article_id(link, title):
    key = (link or "") + "|" + (title or "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


print(f"[1/3] Fetching RSS feeds...")
articles = []

# Domestic RSS
try:
    r = RssNewsConnector().fetch(per_feed=8)
    articles.extend(r.records or [])
    print(f"  rss-news (domestic): {len(r.records or [])} articles")
except Exception as e:
    print(f"  rss-news FAILED: {type(e).__name__}: {e}")

# Mettis Global
try:
    r = MettisGlobalConnector().fetch(per_listing=16)
    mettis_recs = r.records or []
    articles.extend(mettis_recs)
    print(f"  mettis-global: {len(mettis_recs)} articles")
except Exception as e:
    print(f"  mettis-global FAILED: {type(e).__name__}: {e}")

# International
try:
    r = IntlNewsConnector().fetch(per_feed=16)
    intl_recs = r.records or []
    articles.extend(intl_recs)
    print(f"  intl-news: {len(intl_recs)} articles (pre-filtered for PSX relevance)")
except Exception as e:
    print(f"  intl-news FAILED: {type(e).__name__}: {e}")

# Load cache
existing_ids = set()
if CACHE_PATH.exists():
    df = pd.read_parquet(CACHE_PATH)
    existing_ids = set(df["article_id"].tolist())
    print(f"\n[2/3] Cache has {len(df)} scored articles ({len(existing_ids)} unique ids)")
else:
    print(f"\n[2/3] No existing cache.")

# Drop dups
unscored = []
for a in articles:
    aid = article_id(a.get("link", ""), a.get("title", ""))
    if aid in existing_ids:
        continue
    a["article_id"] = aid
    # Compact for agent reading
    unscored.append({
        "article_id":   aid,
        "source":       a.get("source") or "",
        "published_at": a.get("published_at") or "",
        "title":        (a.get("title") or "")[:240],
        "summary":      (a.get("summary") or "")[:500],
        "link":         a.get("link") or "",
        "ticker_hits_hint": a.get("ticker_hits") or [],
    })

print(f"\n[3/3] {len(unscored)} new articles to score (skipped {len(articles) - len(unscored)} already-cached)")

PENDING.write_text(json.dumps(unscored, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"  -> wrote {PENDING}")

# Print preview
print("\nFirst 5 articles preview:")
for a in unscored[:5]:
    print(f"  [{a['source']}] {a['title'][:80]}")

# Stats
from collections import Counter
src_counts = Counter(a["source"] for a in unscored)
print(f"\nBy source ({len(unscored)} total):")
for src, n in src_counts.most_common():
    print(f"  {src:<30} {n}")

print(f"\nUniverse for grounding: {sorted(UNIVERSE)}")
