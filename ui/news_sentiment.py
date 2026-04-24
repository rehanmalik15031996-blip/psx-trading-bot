"""Loader + aggregation helpers for scored news sentiment.

The scorer script (`scripts/score_news_sentiment.py`) writes
`data/news/scored_news.parquet`. This module reads it and produces:

  - `load_scored_news(max_age_hours)` : recent scored articles as DataFrame
  - `macro_sentiment(hours)`          : weighted macro/policy sentiment
  - `ticker_sentiment(symbol, hours)` : weighted sentiment for one ticker
  - `sentiment_block(...)`            : plain-text briefing block for LLM

Weights used in aggregation:
  confidence HIGH = 1.0, MED = 0.6, LOW = 0.25
  older articles decay linearly with half-life = hours / 2
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "news" / "scored_news.parquet"

CONF_WEIGHT = {"HIGH": 1.0, "MED": 0.6, "LOW": 0.25}
MACRO_CATS = {"MACRO", "POLICY", "COMMODITY", "GLOBAL", "GEOPOLITICS"}


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def load_scored_news(max_age_hours: float = 48.0) -> pd.DataFrame:
    if not CACHE_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(CACHE_PATH)
    if df.empty:
        return df
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)

    def _ts(row) -> datetime:
        # Prefer published_at, fall back to scored_at
        for col in ("published_at", "scored_at"):
            dt = _parse_ts(row.get(col, ""))
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
        return now  # give it max freshness if unknown

    df = df.copy()
    df["_ts"] = df.apply(_ts, axis=1)
    df = df[df["_ts"] >= cutoff].copy()
    age_h = (now - df["_ts"]).dt.total_seconds() / 3600.0
    df["_age_h"] = age_h.astype(float)
    df["_conf_w"] = df["confidence"].map(CONF_WEIGHT).fillna(0.25)
    # Linear decay to 0 at max_age_hours, never above 1
    df["_age_w"] = np.clip(1.0 - (df["_age_h"] / max(max_age_hours, 1e-6)),
                             0.0, 1.0)
    df["_w"] = df["_conf_w"] * df["_age_w"]
    return df


def _weighted_mean(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    wsum = float(df["_w"].sum())
    if wsum <= 0:
        return 0.0
    return float((df["sentiment"] * df["_w"]).sum() / wsum)


def macro_sentiment(hours: float = 24.0,
                    df: pd.DataFrame | None = None) -> dict:
    if df is None:
        df = load_scored_news(hours)
    if df.empty:
        return {"n": 0, "score": 0.0, "hours": hours}
    macro = df[df["category"].isin(MACRO_CATS)].copy()
    if macro.empty:
        return {"n": 0, "score": 0.0, "hours": hours}
    return {
        "n": int(len(macro)),
        "score": round(_weighted_mean(macro), 3),
        "hours": hours,
        "by_category": macro.groupby("category")["sentiment"].mean()
                              .round(3).to_dict(),
    }


def ticker_sentiment(symbol: str,
                     hours: float = 72.0,
                     df: pd.DataFrame | None = None) -> dict:
    if df is None:
        df = load_scored_news(hours)
    if df.empty:
        return {"symbol": symbol, "n": 0, "score": 0.0}
    sym = symbol.upper()
    mask = df["affected_symbols"].fillna("").apply(
        lambda s: sym in [x.strip().upper() for x in s.split(",") if x.strip()])
    rows = df[mask].copy()
    if rows.empty:
        return {"symbol": sym, "n": 0, "score": 0.0}
    return {
        "symbol": sym,
        "n": int(len(rows)),
        "score": round(_weighted_mean(rows), 3),
        "latest_title": rows.sort_values("_ts", ascending=False)
                              .iloc[0]["title"][:100],
    }


def sentiment_block(hours_macro: float = 24.0,
                    hours_ticker: float = 72.0,
                    symbols: list[str] | None = None,
                    top_headlines: int = 5) -> str:
    """Plain-text block for LLM briefings."""
    df = load_scored_news(max(hours_macro, hours_ticker))
    if df.empty:
        return ("SCORED NEWS SENTIMENT: no scored news cache yet. "
                "Run scripts/score_news_sentiment.py to populate.")

    macro = macro_sentiment(hours_macro, df)
    lines = ["SCORED NEWS SENTIMENT (weighted by confidence + recency):"]
    arrow = ("BULLISH" if macro["score"] > 0.1
              else "BEARISH" if macro["score"] < -0.1 else "NEUTRAL")
    lines.append(
        f"  Macro/policy tilt ({hours_macro:.0f}h): {macro['score']:+.3f} "
        f"[{arrow}]   n={macro['n']}"
    )
    if macro.get("by_category"):
        cats = "  ".join(f"{k}={v:+.2f}" for k, v in macro["by_category"].items())
        lines.append(f"    by category: {cats}")

    if symbols:
        any_ticker = False
        lines.append(f"  Ticker-specific tilt ({hours_ticker:.0f}h):")
        for sym in symbols:
            t = ticker_sentiment(sym, hours_ticker, df)
            if t["n"] == 0:
                continue
            any_ticker = True
            lines.append(
                f"    {sym:<6s} n={t['n']:<3d} score={t['score']:+.3f} "
                f"| {t.get('latest_title','')}"
            )
        if not any_ticker:
            lines.append("    (no ticker-specific scored news in window)")

    if top_headlines:
        df = df.copy()
        df["abs"] = df["sentiment"].abs() * df["_w"]
        top = df.sort_values("abs", ascending=False).head(top_headlines)
        lines.append(f"  Top {len(top)} impactful headlines:")
        for _, r in top.iterrows():
            tick = f"[{r['affected_symbols']}]" if r["affected_symbols"] else ""
            lines.append(
                f"    {r['sentiment']:+.2f} {r['confidence']:>4s} "
                f"{r['category']:<11s} {tick} {r['title'][:100]}"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    from config.universe import symbols as uni_syms
    print(sentiment_block(symbols=uni_syms()))
    sys.exit(0)
