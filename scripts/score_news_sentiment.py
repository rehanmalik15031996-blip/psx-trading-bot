"""News sentiment scorer.

Pulls fresh articles from the RSS connector, asks Claude Haiku to score
each one for PSX market impact (sentiment, confidence, affected tickers),
and appends to a parquet cache at `data/news/scored_news.parquet`.

Run daily (or multiple times per day) to build a growing time-series of
scored sentiment the live prediction engine can aggregate over.

  python scripts/score_news_sentiment.py
  python scripts/score_news_sentiment.py --per-feed 10 --batch 8
  python scripts/score_news_sentiment.py --rescore   # re-score everything

Schema of scored_news.parquet:
  article_id         str   sha1(link) — primary key, dedup
  published_at       str   ISO-ish timestamp string from feed
  scored_at          str   UTC ISO timestamp
  source             str   feed label (e.g. "Dawn Business")
  title              str
  link               str
  summary            str   first 300 chars from feed
  sentiment          f64   -1.0 (very bearish) .. +1.0 (very bullish)
  confidence         str   LOW | MED | HIGH
  category           str   MACRO | POLICY | COMPANY | COMMODITY |
                           GLOBAL | GEOPOLITICS | OTHER
  affected_symbols   str   comma-joined tickers from universe (or "")
  one_liner          str   <= 120 chars: why this matters for PSX
  model              str   scorer LLM id
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ROOT / ".env")

import pandas as pd

from config.universe import symbols as universe_symbols
from connectors.rss_news import RssNewsConnector
from connectors.mettis_global import MettisGlobalConnector
from connectors.intl_news import IntlNewsConnector

CACHE_DIR = ROOT / "data" / "news"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = CACHE_DIR / "scored_news.parquet"

UNIVERSE = set(universe_symbols())

SCORER_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = f"""You score financial news headlines for likely impact on
the Pakistan Stock Exchange (PSX / KSE-100) over the NEXT 1-5 trading days.

For every article, return one JSON object with these fields:
  sentiment   : float in [-1.0, 1.0]  (-1 very bearish for PSX, 0 neutral,
                +1 very bullish; use small magnitudes for routine news)
  confidence  : "LOW" | "MED" | "HIGH"
                (HIGH only when the article directly names a catalyst that
                moves PSX — rate cut, big FII flow, IMF tranche, etc.)
  category    : "MACRO" | "POLICY" | "COMPANY" | "COMMODITY" |
                "GLOBAL" | "GEOPOLITICS" | "OTHER"
  affected_symbols : list of PSX tickers from this universe that are
                directly mentioned or clearly implicated. Empty list if
                none. Universe: {sorted(UNIVERSE)}
  one_liner   : <= 120 chars, plain English: WHY this matters for PSX.

Grounding rules:
- Do NOT invent tickers. If you aren't sure a company is in the universe,
  leave affected_symbols = []. Index-level drivers should set category to
  MACRO/POLICY/COMMODITY/GLOBAL and leave affected_symbols = [].
- "Oil up" -> +0.2-0.4 for E&P-heavy index, cat=COMMODITY.
- "SBP cuts rate" -> +0.3-0.5, cat=POLICY, high confidence.
- Routine corporate/HR/ceremonial announcements -> near 0 with LOW conf.
- Terror/political escalation in Pakistan -> negative, cat=GEOPOLITICS.
- Global risk-off (US equity down, VIX spike) -> negative, cat=GLOBAL.

Return format: ONE valid JSON ARRAY with one element per article, in
the SAME ORDER as the input list. No prose, no markdown fences.
"""


def _article_id(link: str, title: str) -> str:
    key = (link or "") + "|" + (title or "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _load_existing() -> pd.DataFrame:
    if not CACHE_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(CACHE_PATH)


def _save(df: pd.DataFrame) -> None:
    df.to_parquet(CACHE_PATH, index=False)


def _fetch_articles(per_feed: int) -> list[dict]:
    """Pull raw articles from every news source.

    Combines:
      - The domestic RSS aggregator (Business Recorder, Dawn, Profit,
        Tribune, The News).
      - The Mettis Global scraper (PSX corporate notices + market
        coverage). Each Mettis article carries a best-effort
        ``ticker_hits`` column the LLM scorer can use as grounding.
      - The international RSS aggregator (Reuters, Bloomberg public,
        Investing.com, MarketWatch, Google News custom queries).
        Pre-filtered for Pakistan-relevance via a keyword whitelist
        in ``connectors.intl_news`` so we don't burn LLM credit on
        irrelevant US tech / European earnings stories.

    All three streams use the same record schema so the rest of the
    scoring pipeline is source-agnostic. Failures in any single
    source are logged but do not abort the run.
    """
    rss_result = RssNewsConnector().fetch(per_feed=per_feed)
    rss_records = rss_result.records or []
    try:
        mettis_result = MettisGlobalConnector().fetch(per_listing=per_feed * 2)
        mettis_records = mettis_result.records or []
    except Exception as e:
        print(f"  WARN: Mettis Global fetch failed: {type(e).__name__}: {e}")
        mettis_records = []
    try:
        intl_result = IntlNewsConnector().fetch(per_feed=per_feed * 2)
        intl_records = intl_result.records or []
        if intl_records:
            print(f"  intl-news: {len(intl_records)} Pakistan-relevant "
                  f"articles after global pre-filter")
    except Exception as e:
        print(f"  WARN: International news fetch failed: "
              f"{type(e).__name__}: {e}")
        intl_records = []
    return rss_records + mettis_records + intl_records


def _parse_json_loose(text: str) -> list[dict]:
    """Extract a JSON array from a possibly-noisy LLM response."""
    text = text.strip()
    # Strip code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    # Try direct parse
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    except Exception:
        pass
    # Find the first [...] block
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"Could not parse JSON array from: {text[:200]}")


def _score_batch(batch: list[dict], client) -> list[dict]:
    """Send a batch of articles in one Claude call."""
    compact = []
    for i, a in enumerate(batch):
        item = {
            "i": i,
            "source": a.get("source", ""),
            "published_at": a.get("published_at", ""),
            "title": (a.get("title") or "")[:200],
            "summary": (a.get("summary") or "")[:300],
        }
        # Mettis Global articles carry a coarse ticker hint we surface
        # to the scorer so it doesn't have to re-derive the symbols.
        if a.get("ticker_hits"):
            item["ticker_hits_hint"] = a["ticker_hits"]
        compact.append(item)
    user = (
        f"Score these {len(compact)} articles. Return a JSON ARRAY of "
        f"length {len(compact)} in the same order.\n\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )
    resp = client.messages.create(
        model=SCORER_MODEL,
        max_tokens=min(4000, 120 + 180 * len(compact)),
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text").strip()
    scored = _parse_json_loose(text)
    if len(scored) != len(batch):
        print(f"  WARN: asked for {len(batch)} scores, got {len(scored)} — "
              f"padding with zeros")
        while len(scored) < len(batch):
            scored.append({"sentiment": 0.0, "confidence": "LOW",
                            "category": "OTHER", "affected_symbols": [],
                            "one_liner": "(unparseable)"})
    return scored


# ---------------------------------------------------------------------------
# Rule-based fallback scorer (2026-09-01)
#
# Used when no LLM provider is available (as of this writing, ANTHROPIC_API_KEY
# and GEMINI_API_KEY are both invalid and GitHub Models was permanently
# retired 2026-07-30 -- see docs/strategy_evaluation_2026-09-01.md). This is
# deliberately conservative: keyword/company-name matching only, confidence
# capped at MED (never HIGH -- a keyword hit is not the same as an LLM reading
# the article's actual catalyst), and it exists so the automated/scheduled
# runs degrade gracefully instead of hard-failing for months unnoticed. It is
# NOT a substitute for a real Claude pass; ask a Claude Code session to score
# a batch directly (same SYSTEM_PROMPT + schema) for anything that matters.
# ---------------------------------------------------------------------------
_POS_WORDS = {
    "surge": 0.4, "rally": 0.4, "record profit": 0.5, "record high": 0.4,
    "upgrade": 0.35, "approval": 0.3, "approved": 0.3, "disbursed": 0.35,
    "tranche": 0.25, "resolved": 0.3, "recovery": 0.25, "expansion": 0.2,
    "growth": 0.2, "profit": 0.2, "dividend": 0.2, "bonus": 0.15,
    "increase": 0.15, "gain": 0.2, "rebound": 0.25, "cut rate": 0.4,
    "rate cut": 0.4, "easing": 0.25, "surplus": 0.25, "boost": 0.2,
    "strong": 0.15, "beat estimates": 0.35, "acquisition": 0.15,
    "jump": 0.3, "jumps": 0.3, "climb": 0.25, "climbs": 0.25,
    "soar": 0.35, "soars": 0.35, "advance": 0.15, "advances": 0.15,
}
_NEG_WORDS = {
    "plunge": -0.4, "crash": -0.5, "crisis": -0.4, "default": -0.5,
    "downgrade": -0.35, "rejected": -0.3, "delay": -0.2, "delayed": -0.2,
    "shutdown": -0.35, "loss": -0.25, "losses": -0.25, "strike": -0.25,
    "protest": -0.25, "devaluation": -0.35, "decline": -0.2, "fall": -0.2,
    "falls": -0.2, "deficit": -0.2, "rate hike": -0.3, "hike rate": -0.3,
    "tightening": -0.2, "terror": -0.5, "attack": -0.4, "explosion": -0.45,
    "war": -0.4, "conflict": -0.3, "unrest": -0.3, "sanctions": -0.35,
    "miss estimates": -0.3, "weak": -0.15, "concern": -0.15, "risk": -0.1,
    "slip": -0.25, "slips": -0.25, "slipped": -0.25, "tumble": -0.35,
    "tumbles": -0.35, "sink": -0.3, "sinks": -0.3, "drop": -0.2,
    "drops": -0.2, "weigh on": -0.2, "weighs on": -0.2,
    "under pressure": -0.2, "sell-off": -0.35, "selloff": -0.35,
}
# Order matters: checked top-to-bottom, first match wins. Geopolitics/policy
# take priority over commodity/global since a war-driven oil spike is a
# geopolitics story, not a commodity story (matches the SYSTEM_PROMPT's own
# "terror/political escalation -> GEOPOLITICS" framing).
_CATEGORY_KEYWORDS = {
    "GEOPOLITICS": ("terror", "attack", "war", "conflict", "protest",
                     "unrest", "martial law", "no-confidence", "election",
                     "sanctions", "border"),
    "POLICY": ("sbp", "policy rate", "mpc", "monetary policy", "kibor",
               "interest rate", "central bank"),
    "MACRO": ("imf", "reserves", "cpi", "inflation", "current account",
              "trade deficit", "trade balance", "remittance", "gdp",
              "circular debt", "fiscal", "budget"),
    "GLOBAL": ("fed ", "federal reserve", "wall street", "s&p 500",
               "dow jones", "nasdaq", "dollar index", "vix", "us stocks",
               "us treasury"),
    "COMMODITY": ("oil", "brent", "crude", "gold", "cotton", "gas price",
                  "commodity", "opec"),
}


def _score_one_rules(article: dict, universe_by_name: dict[str, str]) -> dict:
    raw_text = f"{article.get('title', '')} {article.get('summary', '')}"
    text = raw_text.lower()

    affected: list[str] = []
    for hint in (article.get("ticker_hits") or []):
        sym = str(hint).strip().upper()
        if sym in UNIVERSE and sym not in affected:
            affected.append(sym)
    for name_lower, sym in universe_by_name.items():
        if name_lower in text and sym not in affected:
            affected.append(sym)
    # Ticker symbols matched case-sensitively with word boundaries against the
    # ORIGINAL text (not lowered) -- short tickers like SYS/TRG/APL would
    # false-positive as substrings of ordinary words ("systems", "trg" in
    # "strong") if matched against lowercased text.
    for sym in UNIVERSE:
        if sym in affected or len(sym) < 3:
            continue
        if re.search(rf"\b{re.escape(sym)}\b", raw_text):
            affected.append(sym)

    sentiment = 0.0
    hits = 0
    for word, weight in _POS_WORDS.items():
        if word in text:
            sentiment += weight
            hits += 1
    for word, weight in _NEG_WORDS.items():
        if word in text:
            sentiment += weight
            hits += 1
    sentiment = max(-1.0, min(1.0, sentiment))

    category = "COMPANY" if affected else "OTHER"
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            category = cat
            break

    confidence = "MED" if hits >= 2 and abs(sentiment) >= 0.3 else "LOW"

    one_liner = (
        f"Rule-based: {hits} keyword hit(s), category={category}"
        + (f", tickers={','.join(affected)}" if affected else "")
    )[:120]

    return {
        "sentiment": round(sentiment, 3),
        "confidence": confidence,
        "category": category,
        "affected_symbols": affected,
        "one_liner": one_liner,
    }


def _score_batch_rules(batch: list[dict]) -> list[dict]:
    from config.universe import UNIVERSE as _UNIV_ENTRIES
    universe_by_name = {e.name.lower(): e.symbol for e in _UNIV_ENTRIES}
    return [_score_one_rules(a, universe_by_name) for a in batch]


def _clean_symbols(syms) -> str:
    if not syms:
        return ""
    out: list[str] = []
    for s in syms:
        s = str(s).strip().upper()
        if s in UNIVERSE and s not in out:
            out.append(s)
    return ",".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-feed", type=int, default=5,
                        help="articles to pull per RSS feed")
    parser.add_argument("--batch", type=int, default=8,
                        help="articles per Claude call")
    parser.add_argument("--rescore", action="store_true",
                        help="ignore cache, re-score every article")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap total articles scored this run")
    parser.add_argument("--skip-health", action="store_true",
                        help="Do not call _health.write_status. Set when "
                             "this script runs as a sub-step inside "
                             "another workflow (e.g. intraday_session) "
                             "so two workflows don't fight over "
                             "data/_health/news_scoring.json.")
    args = parser.parse_args()

    print(f"[1/4] Fetching RSS feeds (per_feed={args.per_feed}) ...")
    articles = _fetch_articles(args.per_feed)
    print(f"      got {len(articles)} articles")

    existing = _load_existing()
    seen_ids = set(existing["article_id"].tolist()) if not existing.empty else set()
    print(f"[2/4] Cache has {len(existing)} scored articles "
          f"(ids seen: {len(seen_ids)})")

    # Attach IDs and drop duplicates unless --rescore
    enriched = []
    for a in articles:
        aid = _article_id(a.get("link", ""), a.get("title", ""))
        if aid in seen_ids and not args.rescore:
            continue
        a["_article_id"] = aid
        enriched.append(a)
    if args.limit:
        enriched = enriched[: args.limit]
    print(f"[3/4] {len(enriched)} new articles to score "
          f"(rescore={args.rescore})")

    if not enriched:
        print("Nothing to score. Done.")
        return 0

    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    else:
        print("  ANTHROPIC_API_KEY not set -- using rule-based fallback "
              "for this whole run.")

    scored_rows: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    used_fallback = client is None
    for i in range(0, len(enriched), args.batch):
        chunk = enriched[i : i + args.batch]
        print(f"  scoring batch {i // args.batch + 1} "
              f"({len(chunk)} articles) ...")
        t0 = time.perf_counter()
        model_tag = SCORER_MODEL
        if client is not None:
            try:
                scores = _score_batch(chunk, client)
            except Exception as e:
                print(f"    batch failed: {type(e).__name__}: {e} "
                      f"-- switching to rule-based fallback for the "
                      f"rest of this run.")
                client = None
                used_fallback = True
        if client is None:
            scores = _score_batch_rules(chunk)
            model_tag = "rules-fallback-v1"
        dt = (time.perf_counter() - t0) * 1000
        print(f"    done in {dt:.0f}ms ({'Claude' if model_tag == SCORER_MODEL else 'rules fallback'})")

        for art, sc in zip(chunk, scores):
            try:
                sent = float(sc.get("sentiment", 0) or 0)
            except Exception:
                sent = 0.0
            sent = max(-1.0, min(1.0, sent))
            scored_rows.append({
                "article_id": art["_article_id"],
                "published_at": art.get("published_at") or "",
                "scored_at": now_iso,
                "source": art.get("source") or "",
                "title": (art.get("title") or "")[:300],
                "link": art.get("link") or "",
                "summary": (art.get("summary") or "")[:300],
                "sentiment": round(sent, 3),
                "confidence": str(sc.get("confidence", "LOW")).upper()[:4],
                "category": str(sc.get("category", "OTHER")).upper()[:14],
                "affected_symbols": _clean_symbols(sc.get("affected_symbols", [])),
                "one_liner": str(sc.get("one_liner", ""))[:160],
                "model": model_tag,
            })

    if not scored_rows:
        print("No new rows scored (all batches failed?).")
        return 1

    new_df = pd.DataFrame(scored_rows)
    if existing.empty:
        out = new_df
    elif args.rescore:
        # keep the newest score per article_id
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.sort_values("scored_at").drop_duplicates(
            "article_id", keep="last")
        out = combined
    else:
        out = pd.concat([existing, new_df], ignore_index=True)

    _save(out)

    print(f"[4/4] Wrote {len(new_df)} new rows; cache now = {len(out)}.")
    # Quick summary
    cat_counts = new_df["category"].value_counts().to_dict()
    avg_sent = round(float(new_df["sentiment"].mean()), 3)
    print(f"  Avg sentiment of this batch: {avg_sent}")
    print(f"  By category: {cat_counts}")
    # Top 5 by |sentiment| for eyeball check
    new_df["abs"] = new_df["sentiment"].abs()
    top = new_df.sort_values("abs", ascending=False).head(5)
    print("\n  Top 5 most impactful (absolute):")
    for _, r in top.iterrows():
        tickers = f" [{r['affected_symbols']}]" if r["affected_symbols"] else ""
        print(f"    {r['sentiment']:+.2f} {r['confidence']:>4s} "
              f"{r['category']:<10s}{tickers} | {r['title'][:80]}")

    if not args.skip_health:
        try:
            from scripts._health import write_status
            write_status(
                workflow="news_scoring",
                ok=True,
                note=(f"scored {len(new_df)} new of {len(articles)} fetched"
                      + (" [RULE-BASED FALLBACK -- no LLM key available]"
                         if used_fallback else "")),
                payload={
                    "scored":  int(len(new_df)),
                    "fetched": int(len(articles)),
                    "by_category": {k: int(v)
                                      for k, v in (cat_counts or {}).items()},
                    "used_fallback": used_fallback,
                },
            )
        except Exception as e:
            print(f"  WARN: _health.write_status failed: "
                  f"{type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
