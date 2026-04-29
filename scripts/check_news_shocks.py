"""Intraday news-shock detector.

This script is the "autonomous" half of the bot. The user's repeated
complaint was that the predictions cycle runs once at 09:15 PKT and
cannot react to events that break later in the day — a surprise SBP
hike, a regulator action, an oil-price crash. The result was that
yesterday's BUY call on a rate-sensitive name became today's loss when
the SBP MPC announced a hike at 11:45 mid-session.

Mechanism
---------
Every news-scoring run (07:00 / 13:00 / 18:00 PKT) writes the latest
batch of scored articles to ``data/news/scored_news.parquet``. After
that run, this script:

1. Reads the file and isolates articles published in the last
   ``SHOCK_WINDOW_HOURS`` (default 6).
2. Flags any article that crosses **all three** of these gates:
       * ``|sentiment| >= MIN_SENTIMENT``  (default 0.40)
       * ``confidence == HIGH``
       * touches at least one universe ticker (or is tagged with a
         high-impact macro category like POLICY_RATE, FX, OIL_SHOCK)
3. Writes a tiny ``data/news/shock_log.json`` record per shock so we
   never retrigger predictions on the same article.
4. Exit code 0 = no fresh shock; exit code 7 = SHOCK detected. The
   wrapping GitHub-Actions workflow then dispatches the
   ``predictions.yml`` workflow via ``gh workflow run`` so the bot's
   recommendations refresh within minutes of the shock landing.

Why exit code 7?
~~~~~~~~~~~~~~~~
GitHub Actions treats any non-zero exit as job failure by default. We
keep this script's job marked as success even when a shock fires by
having the workflow's ``run:`` block check the code explicitly:

    python scripts/check_news_shocks.py
    rc=$?
    if [ "$rc" -eq 7 ]; then
        gh workflow run predictions.yml
    fi

Run locally: ``python scripts/check_news_shocks.py``.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Thresholds — calibrated against PSX intraday volatility.
SHOCK_WINDOW_HOURS = 6
MIN_SENTIMENT      = 0.35   # was 0.40 - lowered after April 29 scorecard
                              # showed "Rate hike undermines investor
                              # confidence" (sentiment -0.40) was sitting
                              # right on the threshold.
# Broad-market shock: if the news flow as a whole has flipped direction
# (>= ``BROAD_MARKET_MIN_ARTICLES`` HIGH-confidence articles inside the
# window cross ``MIN_SENTIMENT``), that's itself a shock — even if no
# single article touches a universe ticker.
BROAD_MARKET_MIN_ARTICLES = 3

SHOCK_CATEGORIES   = {
    "POLICY_RATE", "INTEREST_RATE", "MPC", "MONETARY",
    "FX", "RUPEE", "DEVALUATION", "RESERVES",
    "OIL_SHOCK", "OIL_PRICE",
    "REGULATOR", "SBP", "SECP", "OGRA", "NEPRA",
    "DEFAULT", "DOWNGRADE", "S&P", "MOODYS", "FITCH",
    # Broad-market tags so the existing macro path also catches the
    # cluster that hit on April 29 ("KSE-100 retreats 2,588 points",
    # "PSX reverses early gains, closes red"). The broader narrative
    # flipped the entire market regardless of which ticker was named.
    "MARKET", "MARKETS", "KSE", "KSE100", "KSE-100",
    "SELLOFF", "SELL_OFF", "EQUITIES", "INDEX",
    "BROAD_MARKET", "PSX", "BEARISH_REGIME",
}

# Files
ROOT          = Path(__file__).resolve().parent.parent
NEWS_PATH     = ROOT / "data" / "news" / "scored_news.parquet"
SHOCK_LOG     = ROOT / "data" / "news" / "shock_log.json"


def _load_shock_log() -> dict:
    if not SHOCK_LOG.exists():
        return {"version": 1, "shocks": []}
    try:
        return json.loads(SHOCK_LOG.read_text(encoding="utf-8"))
    except Exception:
        # Corrupt log: start over rather than crash the workflow.
        return {"version": 1, "shocks": []}


def _save_shock_log(log: dict) -> None:
    SHOCK_LOG.parent.mkdir(parents=True, exist_ok=True)
    SHOCK_LOG.write_text(json.dumps(log, indent=2,
                                       default=str),
                          encoding="utf-8")


def _load_universe_tickers() -> set[str]:
    try:
        from config.universe import symbols
        return {s.upper() for s in symbols()}
    except Exception:
        return set()


def _hits_universe(affected: object,
                    universe: set[str]) -> set[str]:
    """Return the set of universe tickers an article touches."""
    if not affected or not universe:
        return set()
    if isinstance(affected, (list, tuple)):
        candidates = {str(x).upper() for x in affected}
    elif isinstance(affected, str):
        candidates = {x.strip().upper()
                      for x in affected.split(",") if x.strip()}
    else:
        return set()
    return candidates & universe


def _is_macro_shock(category: object) -> bool:
    if not category:
        return False
    cat_norm = str(category).upper().replace(" ", "_")
    return any(tag in cat_norm for tag in SHOCK_CATEGORIES)


def _detect_broad_market_shock(df, fired_broad_keys: set[str]) -> dict | None:
    """Detect when the broader news narrative flips even if no single
    article matches a universe ticker / macro tag.

    The criterion: >= ``BROAD_MARKET_MIN_ARTICLES`` HIGH-confidence
    articles inside the recent window all carry sentiment in the same
    direction with ``|sentiment| >= MIN_SENTIMENT``. That's a sign the
    overall narrative has tilted bearish (or bullish) and the bot's
    cached predictions need a refresh.

    Returns a single synthetic shock record or ``None``. The shock is
    de-duplicated using a short signature (date + direction + count) so
    the same broad shift only fires the predictions workflow once per
    day per direction.
    """
    if df is None or len(df) == 0:
        return None
    high_conf = df[
        (df["confidence"].astype(str).str.upper() == "HIGH")
        & (df["sentiment"].abs() >= MIN_SENTIMENT)
    ].copy()
    if len(high_conf) < BROAD_MARKET_MIN_ARTICLES:
        return None

    bears = high_conf[high_conf["sentiment"] <= -MIN_SENTIMENT]
    bulls = high_conf[high_conf["sentiment"] >= MIN_SENTIMENT]
    if len(bears) >= BROAD_MARKET_MIN_ARTICLES:
        side = "BEARISH"
        flock = bears
    elif len(bulls) >= BROAD_MARKET_MIN_ARTICLES:
        side = "BULLISH"
        flock = bulls
    else:
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    sig = f"BROAD::{today}::{side}::{len(flock)}"
    if sig in fired_broad_keys:
        return None

    avg_sent = float(flock["sentiment"].mean())
    titles = [str(t)[:120] for t in flock["title"].tolist()[:5]]
    return {
        "article_id": sig,
        "title": (f"Broad-market {side.lower()} shock: "
                    f"{len(flock)} HIGH-confidence articles avg "
                    f"sentiment {avg_sent:+.2f}"),
        "sentiment": avg_sent,
        "confidence": "HIGH",
        "category": "BROAD_MARKET",
        "affected_symbols": [],
        "is_macro_shock": True,
        "is_broad_market": True,
        "side": side,
        "n_articles": int(len(flock)),
        "sample_titles": titles,
        "scored_at": "",
        "detected_at": datetime.now(timezone.utc)
                          .isoformat(timespec="seconds"),
    }


def detect_shocks() -> list[dict]:
    """Return a list of shock records (one per qualifying article).

    Includes one synthetic record at most for a broad-market regime
    shift (``is_broad_market=True``) when several HIGH-confidence
    articles tilt the same direction inside the window.
    """
    if not NEWS_PATH.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(NEWS_PATH)
    except Exception as e:
        print(f"shock-check: cannot read news file: {e}",
              file=sys.stderr)
        return []
    if df.empty:
        return []

    df["_ts"] = pd.to_datetime(df["scored_at"], utc=True,
                                  errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=SHOCK_WINDOW_HOURS)
    df = df[df["_ts"] >= cutoff].copy()
    if df.empty:
        return []

    # Already-fired articles must not retrigger.
    log = _load_shock_log()
    fired_ids = {s.get("article_id") for s in log.get("shocks", [])}

    universe = _load_universe_tickers()
    shocks: list[dict] = []
    for _, r in df.iterrows():
        try:
            sent = float(r.get("sentiment") or 0)
        except (TypeError, ValueError):
            sent = 0.0
        conf = str(r.get("confidence") or "").upper()
        affected = r.get("affected_symbols")
        cat = r.get("category")

        if abs(sent) < MIN_SENTIMENT:
            continue
        if conf != "HIGH":
            continue

        hits = _hits_universe(affected, universe)
        is_macro = _is_macro_shock(cat)
        if not hits and not is_macro:
            continue

        article_id = str(r.get("article_id") or r.get("link") or
                            r.get("title") or "")
        if not article_id or article_id in fired_ids:
            continue

        shocks.append({
            "article_id": article_id,
            "title":      str(r.get("title") or "")[:160],
            "sentiment":  sent,
            "confidence": conf,
            "category":   str(cat or ""),
            "affected_symbols": sorted(list(hits)),
            "is_macro_shock": is_macro,
            "is_broad_market": False,
            "scored_at": str(r.get("scored_at") or ""),
            "detected_at": datetime.now(timezone.utc)
                              .isoformat(timespec="seconds"),
        })

    # Broad-market shock: the narrative-flip case.
    broad = _detect_broad_market_shock(df, fired_ids)
    if broad is not None:
        shocks.append(broad)

    return shocks


def main() -> int:
    shocks = detect_shocks()
    if not shocks:
        print("shock-check: no fresh shocks.")
        return 0

    log = _load_shock_log()
    log.setdefault("shocks", []).extend(shocks)
    # Trim to the most recent 200 entries.
    log["shocks"] = log["shocks"][-200:]
    _save_shock_log(log)

    print(f"shock-check: {len(shocks)} shock(s) detected:")
    for s in shocks:
        sym = ",".join(s["affected_symbols"]) or (
            "MACRO" if s["is_macro_shock"] else "?")
        print(f"  - [{s['confidence']} {s['sentiment']:+.2f}] "
              f"{sym}: {s['title']}")
    return 7


if __name__ == "__main__":
    sys.exit(main())
