"""Lightweight sentiment scoring for PSX news headlines.

Philosophy: a lexicon-based scorer gives every news item a number in [-1, +1]
with near-zero cost (pure Python, no ML install). It's not as accurate as
FinBERT but it's transparent, fast, and good enough to surface directional
signal. The LLM analyst (brain/llm_analyst.py) handles the deeper reasoning.

The lexicon is PSX-tuned: it includes Pakistani political/macro terms
(IMF, SBP, PKR, etc.) that a generic finance lexicon misses.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


POSITIVE_WORDS = {
    "surge", "surges", "surged", "soar", "soared", "jump", "jumps", "jumped",
    "rally", "rallies", "rallied", "gain", "gains", "gained", "rise", "rises",
    "risen", "climb", "climbs", "climbed", "advance", "advances", "advanced",
    "rebound", "rebounds", "rebounded", "recover", "recovers", "recovered",
    "upbeat", "optimistic", "bullish", "strong", "strength", "robust",
    "record", "all-time-high", "peak", "hit-high", "breakout",
    "growth", "growing", "expand", "expansion", "improvement", "improved",
    "beat", "beats", "exceeded", "surpass", "upgrade", "upgraded",
    "inflows", "fipi-inflow", "imf-approval", "imf-disbursement", "imf-deal",
    "bailout", "loan-approved", "rate-cut", "policy-cut", "ease", "easing",
    "reserves-rise", "reserves-up", "rupee-gains", "rupee-strengthens",
    "subsidy", "relief", "stability", "stable",
    "dividend", "bonus", "buyback",
}

NEGATIVE_WORDS = {
    "plunge", "plunges", "plunged", "tumble", "tumbles", "tumbled",
    "crash", "crashes", "crashed", "fall", "falls", "fell", "drop", "drops",
    "dropped", "decline", "declines", "declined", "slide", "slides", "slid",
    "slump", "slumps", "slumped", "sink", "sinks", "sank", "rout",
    "bearish", "weak", "weakness", "sluggish", "gloomy", "pessimistic",
    "selloff", "sell-off", "correction", "meltdown",
    "contraction", "recession", "slowdown", "downgrade", "downgraded",
    "miss", "missed", "underperform", "loss", "losses",
    "outflow", "outflows", "fipi-outflow", "imf-delay", "imf-concern",
    "rate-hike", "policy-hike", "tight", "tightening", "shortage",
    "reserves-fall", "reserves-drop", "rupee-falls", "rupee-weakens",
    "depreciation", "inflation-rise", "default", "crisis", "turmoil",
    "protest", "strike", "political-crisis", "uncertainty", "unrest",
    "terrorist", "attack", "blast",
    "investigation", "probe", "fine", "penalty", "lawsuit",
}

INTENSIFIERS = {"sharp", "sharply", "heavy", "heavily", "massive", "significantly",
                "substantially", "deeply", "strongly"}
NEGATORS = {"no", "not", "never", "without", "despite", "against"}


@dataclass
class SentimentScore:
    score: float
    positive_hits: list[str]
    negative_hits: list[str]
    words_scored: int


def score_text(text: str) -> SentimentScore:
    """Score a single headline/summary using the PSX-tuned lexicon.

    Returns a score in [-1, +1] plus lists of which positive/negative words
    triggered.
    """
    if not text:
        return SentimentScore(0.0, [], [], 0)

    t = text.lower()
    t = re.sub(r"[^\w\s\-]", " ", t)

    for a, b in [
        ("imf approval", "imf-approval"),
        ("imf disbursement", "imf-disbursement"),
        ("imf delay", "imf-delay"),
        ("rate cut", "rate-cut"),
        ("rate hike", "rate-hike"),
        ("fipi inflow", "fipi-inflow"),
        ("fipi outflow", "fipi-outflow"),
        ("sell off", "sell-off"),
        ("rupee gains", "rupee-gains"),
        ("rupee falls", "rupee-falls"),
        ("rupee weakens", "rupee-weakens"),
        ("rupee strengthens", "rupee-strengthens"),
        ("record high", "record"),
        ("all time high", "all-time-high"),
        ("political crisis", "political-crisis"),
    ]:
        t = t.replace(a, b)

    tokens = t.split()
    pos_hits, neg_hits = [], []
    score = 0.0
    n_scored = 0

    for i, tok in enumerate(tokens):
        val = 0.0
        if tok in POSITIVE_WORDS:
            val = 1.0
            pos_hits.append(tok)
        elif tok in NEGATIVE_WORDS:
            val = -1.0
            neg_hits.append(tok)
        else:
            continue

        prev = tokens[max(0, i - 2): i]
        if any(w in INTENSIFIERS for w in prev):
            val *= 1.5
        if any(w in NEGATORS for w in prev):
            val *= -1

        score += val
        n_scored += 1

    if n_scored == 0:
        return SentimentScore(0.0, [], [], 0)

    scaled = math.tanh(score / 3.0)
    return SentimentScore(round(scaled, 3), pos_hits[:5], neg_hits[:5], n_scored)


SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "OGDC":   ["ogdc", "oil and gas development", "ogdcl"],
    "PPL":    ["ppl ", "pakistan petroleum"],
    "MARI":   ["mari petroleum", "mari gas"],
    "PSO":    ["pso ", "pakistan state oil"],
    "MCB":    ["mcb bank"],
    "HBL":    ["hbl ", "habib bank"],
    "UBL":    ["ubl ", "united bank"],
    "MEBL":   ["meezan bank", "mebl"],
    "LUCK":   ["lucky cement"],
    "FCCL":   ["fauji cement", "fccl"],
    "FFC":    ["fauji fertilizer"],
    "EFERT":  ["engro fertilizer", "efert"],
    "HUBC":   ["hub power", "hubco", "hubc"],
    "ENGROH": ["engro corp", "engro holdings", "engroh"],
    "SYS":    ["systems limited", "systems ltd"],
}


def score_news_for_symbol(news_records: list[dict], symbol: str) -> dict:
    """Aggregate sentiment for one symbol across today's news."""
    kws = SYMBOL_KEYWORDS.get(symbol, [symbol.lower()])
    relevant = []
    for n in news_records:
        blob = (n.get("title", "") + " " + n.get("summary", "")).lower()
        if any(kw in blob for kw in kws):
            relevant.append(n)

    if not relevant:
        return {"score": 0.0, "n_articles": 0, "headlines": []}

    scores = []
    all_pos, all_neg = [], []
    heads = []
    for n in relevant[:10]:
        s = score_text(n.get("title", "") + ". " + n.get("summary", ""))
        scores.append(s.score)
        all_pos.extend(s.positive_hits)
        all_neg.extend(s.negative_hits)
        heads.append({"title": n.get("title", ""), "score": s.score})

    return {
        "score": round(sum(scores) / len(scores), 3),
        "n_articles": len(relevant),
        "positive_hits": list(set(all_pos))[:8],
        "negative_hits": list(set(all_neg))[:8],
        "headlines": heads,
    }


def score_market_sentiment(news_records: list[dict]) -> dict:
    """Market-wide sentiment across ALL news — a macro regime feature."""
    if not news_records:
        return {"score": 0.0, "n_articles": 0}
    scores = []
    for n in news_records[:50]:
        s = score_text(n.get("title", "") + ". " + n.get("summary", ""))
        scores.append(s.score)
    return {
        "score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "n_articles": len(scores),
    }


if __name__ == "__main__":
    samples = [
        "KSE-100 plunges below 170,000-mark as bears maintain control of PSX",
        "Rupee gains sharply against dollar on strong IMF disbursement",
        "Cement exports surge to record high amid growing domestic demand",
        "Political crisis deepens as protests spread",
        "OGDC announces bonus dividend; share price rallies",
    ]
    for s in samples:
        r = score_text(s)
        sign = "+" if r.score >= 0 else ""
        print(f"{sign}{r.score:.3f}  [{','.join(r.positive_hits)}] / [{','.join(r.negative_hits)}]  :: {s}")
