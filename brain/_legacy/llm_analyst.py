"""LLM analyst: narrative overlay on ML signals.

For each shortlisted stock (those the ML model says to BUY or HOLD), we ask
Claude Haiku to cross-check the quantitative signal against:
  - Recent news flow and its sentiment
  - Macro backdrop (policy rate, PKR/USD, commodity levels)
  - Any red flags (political, regulatory, corporate action)

The LLM returns a STRUCTURED JSON verdict:
  {
    "verdict": "STRONG_BUY" | "BUY" | "HOLD" | "AVOID",
    "confidence": 0.0..1.0,
    "rationale": "one sentence why",
    "key_risks": ["...", "..."]
  }

This verdict is then combined with the ML probability and the risk manager
to produce final trade decisions.

Cost: ~$0.001-0.003 per shortlisted stock with Claude Haiku. At 15 stocks
daily that's ~$0.03/day = ~$1/month.

If no API key is set (ANTHROPIC_API_KEY env var), the analyst returns
neutral verdicts with confidence 0.5 so the rest of the pipeline still works.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = """You are a disciplined, skeptical buy-side analyst covering \
the Pakistan Stock Exchange (PSX). You see the ML model's quantitative signal \
and today's news and macro context. Your job is to either confirm the signal \
or flag that something in the news/macro backdrop invalidates it.

Rules:
- Return ONLY valid JSON matching the specified schema. No prose outside JSON.
- Be honest: if news is mixed or thin, say HOLD, not BUY.
- Key red flags to veto BUY: political crisis, regulatory action against the \
company, CEO departure, default risk, circuit breakers, material adverse news.
- Key tailwinds: IMF disbursement, rate cut, positive corporate action \
(dividend, bonus, buyback), strong sector news.
- Do NOT invent facts. If you don't know, say so in rationale.
- confidence 0.8+ only if news clearly aligns with the ML signal.
"""


@dataclass
class AnalystVerdict:
    symbol: str
    verdict: str          # STRONG_BUY | BUY | HOLD | AVOID
    confidence: float     # [0, 1]
    rationale: str
    key_risks: list[str] = field(default_factory=list)
    ml_prob_up: float | None = None
    sentiment_score: float | None = None
    raw_response: str = ""


VALID_VERDICTS = {"STRONG_BUY", "BUY", "HOLD", "AVOID"}


def _build_prompt(
    symbol: str,
    company_name: str,
    sector: str,
    ml_prob_up: float,
    recent_return_5d: float | None,
    sentiment: dict,
    macro_context: dict,
    news_snippets: list[dict],
) -> str:
    lines = [
        f"# Stock: {symbol} ({company_name}, {sector})",
        "",
        "## Quantitative signal (from per-stock ML ensemble)",
        f"- P(up in next 5 days): {ml_prob_up:.3f}",
        (f"- Recent 5-day return: {recent_return_5d:+.2%}"
         if recent_return_5d is not None else "- Recent 5d return: n/a"),
        "",
        "## News sentiment (lexicon-scored)",
        f"- Aggregate score: {sentiment.get('score', 0):+.2f}  (-1=very bad, +1=very good)",
        f"- Articles mentioning this stock: {sentiment.get('n_articles', 0)}",
    ]
    if sentiment.get("positive_hits"):
        lines.append(f"- Positive triggers: {', '.join(sentiment['positive_hits'])}")
    if sentiment.get("negative_hits"):
        lines.append(f"- Negative triggers: {', '.join(sentiment['negative_hits'])}")

    lines.extend([
        "",
        "## Macro context (today)",
        f"- PKR/USD: {macro_context.get('usdpkr', 'n/a')}",
        f"- KSE-100 change today: {macro_context.get('kse100_change_pct', 'n/a')}",
        f"- Policy rate: {macro_context.get('policy_rate', 'n/a')}",
        f"- Brent: {macro_context.get('brent', 'n/a')}",
        f"- FIPI today (PKR mn): {macro_context.get('fipi_net', 'n/a')}",
        "",
        "## Recent news snippets",
    ])

    if news_snippets:
        for i, n in enumerate(news_snippets[:8], 1):
            title = n.get("title", "")
            score = n.get("score", 0)
            lines.append(f"{i}. [{score:+.2f}] {title}")
    else:
        lines.append("(no stock-specific news in last 24h)")

    lines.extend([
        "",
        "## Your task",
        "Return a JSON object with exactly these fields:",
        "  - verdict: STRONG_BUY | BUY | HOLD | AVOID",
        "  - confidence: float in [0, 1]",
        "  - rationale: one sentence explaining the verdict",
        '  - key_risks: array of 1-3 short strings (can be empty [])',
        "Do not output anything else.",
    ])

    return "\n".join(lines)


def _neutral_fallback(symbol: str, ml_prob_up: float, sentiment_score: float) -> AnalystVerdict:
    """Produce a safe verdict when no LLM is available."""
    if ml_prob_up >= 0.65 and sentiment_score >= 0:
        verdict, conf = "BUY", 0.6
    elif ml_prob_up >= 0.55 and sentiment_score >= -0.3:
        verdict, conf = "HOLD", 0.5
    elif ml_prob_up < 0.45 or sentiment_score < -0.5:
        verdict, conf = "AVOID", 0.5
    else:
        verdict, conf = "HOLD", 0.5
    return AnalystVerdict(
        symbol=symbol,
        verdict=verdict,
        confidence=conf,
        rationale=(
            f"[LLM disabled] Rule-based fallback using ML prob={ml_prob_up:.2f} "
            f"and sentiment={sentiment_score:+.2f}."
        ),
        key_risks=[],
        ml_prob_up=ml_prob_up,
        sentiment_score=sentiment_score,
    )


def _parse_json_verdict(raw: str) -> dict | None:
    """Parse the LLM response, tolerant of code fences and stray whitespace."""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        # Strip code fence
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    # Find first '{' and last '}' to be safe
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1:
        return None
    try:
        return json.loads(s[i: j + 1])
    except json.JSONDecodeError:
        return None


def analyze_symbol(
    symbol: str,
    company_name: str,
    sector: str,
    ml_prob_up: float,
    recent_return_5d: float | None,
    sentiment: dict,
    macro_context: dict,
    news_snippets: list[dict],
    model: str = "claude-haiku-4-5",
) -> AnalystVerdict:
    """Call Claude for one stock. Falls back to rule-based if no key present."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _neutral_fallback(symbol, ml_prob_up, sentiment.get("score", 0.0))

    try:
        from anthropic import Anthropic
    except ImportError:
        return _neutral_fallback(symbol, ml_prob_up, sentiment.get("score", 0.0))

    prompt = _build_prompt(
        symbol, company_name, sector, ml_prob_up, recent_return_5d,
        sentiment, macro_context, news_snippets,
    )

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception as e:
        v = _neutral_fallback(symbol, ml_prob_up, sentiment.get("score", 0.0))
        v.rationale = f"[LLM call failed: {type(e).__name__}] {v.rationale}"
        return v

    parsed = _parse_json_verdict(raw)
    if not parsed or parsed.get("verdict") not in VALID_VERDICTS:
        v = _neutral_fallback(symbol, ml_prob_up, sentiment.get("score", 0.0))
        v.rationale = f"[LLM response unparseable] {v.rationale}"
        v.raw_response = raw[:500]
        return v

    return AnalystVerdict(
        symbol=symbol,
        verdict=parsed["verdict"],
        confidence=float(parsed.get("confidence", 0.5)),
        rationale=str(parsed.get("rationale", ""))[:300],
        key_risks=list(parsed.get("key_risks", []))[:3],
        ml_prob_up=ml_prob_up,
        sentiment_score=sentiment.get("score", 0.0),
        raw_response=raw[:500],
    )


def analyze_shortlist(
    shortlist: list[dict],
    macro_context: dict,
    news_by_symbol: dict[str, dict],
    model: str = "claude-haiku-4-5",
) -> list[AnalystVerdict]:
    """Run the analyst over a list of candidate trades.

    Each shortlist entry should be a dict with keys:
      symbol, company_name, sector, ml_prob_up, recent_return_5d (optional)
    """
    verdicts = []
    for s in shortlist:
        sym = s["symbol"]
        sentiment = news_by_symbol.get(sym, {"score": 0.0, "n_articles": 0})
        v = analyze_symbol(
            symbol=sym,
            company_name=s.get("company_name", sym),
            sector=s.get("sector", ""),
            ml_prob_up=float(s.get("ml_prob_up", 0.5)),
            recent_return_5d=s.get("recent_return_5d"),
            sentiment=sentiment,
            macro_context=macro_context,
            news_snippets=sentiment.get("headlines", []),
            model=model,
        )
        verdicts.append(v)
    return verdicts


if __name__ == "__main__":
    # Smoke test (no API key -> uses rule-based fallback)
    v = analyze_symbol(
        symbol="OGDC",
        company_name="Oil & Gas Development Co.",
        sector="Energy",
        ml_prob_up=0.68,
        recent_return_5d=0.042,
        sentiment={"score": 0.35, "n_articles": 3,
                   "positive_hits": ["dividend"], "negative_hits": []},
        macro_context={"usdpkr": 278, "kse100_change_pct": 0.8,
                       "policy_rate": 11, "brent": 85, "fipi_net": -120},
        news_snippets=[{"title": "OGDC announces interim dividend", "score": 0.7}],
    )
    print(f"Verdict: {v.verdict}  Confidence: {v.confidence:.2f}")
    print(f"Rationale: {v.rationale}")
    print(f"Key risks: {v.key_risks}")
