"""Generate dated predictions for the user's 6 required tickers.

For each of HUBC, PABC, MLCF, OGDC, FABL, PPL:
  1. Gather a full data bundle via ui.tools.get_full_context (price, technicals,
     momentum rank, Phase 1 signal, symbol-matched news, FIPI flows, macro
     snapshot, SBP policy rate).
  2. Ask the LLM (Claude or Gemini, whichever has a key) to return a strict
     JSON prediction for the next 5 trading days: direction, conviction,
     expected return range, suggested action, stop/target, drivers, risks.
  3. If no LLM key is set, fall back to a rule-based prediction (no news).
  4. Append the prediction to data/predictions_log.json along with a full
     data snapshot so we can score it later.

Run:  python scripts/generate_predictions.py            # auto-detect provider
      python scripts/generate_predictions.py --model claude
      python scripts/generate_predictions.py --model rule
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is on sys.path when invoked directly
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force UTF-8 stdout on Windows so emoji / arrows don't crash the pipe
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _load_dotenv(path: Path) -> None:
    """Tiny inline .env loader. Values already in the environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ROOT / ".env")

import pandas as pd

from config.candidates import REQUIRED_TICKERS
from ui.overnight import build_overnight_block, gap_bias_from_overnight, load_overnight
from ui.news_sentiment import sentiment_block as scored_sentiment_block
from ui.tools import get_full_context

LOG_PATH = ROOT / "data" / "predictions_log.json"
HORIZON_DAYS = 5


# ==========================================================================
# Provider detection
# ==========================================================================
def detect_provider() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "rule"


# ==========================================================================
# LLM prompt builders
# ==========================================================================
PREDICTION_SYSTEM_PROMPT = """You are a quantitative analyst generating
short-horizon predictions for PSX stocks. You MUST ground every claim in the
data briefing below. Never invent numbers or news headlines.

Your output MUST be a single valid JSON object, no prose, no markdown fences.
The schema is:
{
  "direction": "BULLISH" | "NEUTRAL" | "BEARISH",
  "conviction": "LOW" | "MEDIUM" | "HIGH",
  "expected_return_5d_low_pct": <float, e.g. -2.5>,
  "expected_return_5d_mid_pct": <float>,
  "expected_return_5d_high_pct": <float>,
  "suggested_action": "BUY" | "ADD" | "HOLD" | "TRIM" | "AVOID" | "SELL",
  "suggested_stop_pkr": <float>,
  "suggested_target_pkr": <float>,
  "rationale": "<2-3 sentence paragraph>",
  "key_drivers": ["<short bullet>", "<short bullet>"],
  "key_risks": ["<short bullet>", "<short bullet>"]
}

Calibration guidance (IMPORTANT):
- Most 5-day moves on PSX are ±2-6%. A "BULLISH" call should expect the mid
  return to be positive but rarely >6% unless the stock is strongly trending.
- HIGH conviction requires multiple data layers to agree (momentum + news +
  flows + macro + overnight global risk). If any major layer disagrees,
  use MEDIUM.
- LOW conviction is appropriate when signals are mixed or the market filter
  is off, or when the overnight global prior contradicts the stock's trend.
- Suggested stop is typically 6-10% below current price for swing horizon.
- If suggested_action is AVOID or SELL, still provide stop/target for context.
- TRANSACTION COSTS: PSX round-trip = ~0.56% (brokerage + FED + slippage) and
  CGT on gains = 15%. A "BUY"/"ADD" call only makes sense if
  expected_return_5d_mid_pct >= ~1.6% (cost + 1.0% minimum edge). If your
  conviction-weighted mid is below 1.6%, use suggested_action = "HOLD"
  instead of "BUY"/"ADD".
- SCORED NEWS SENTIMENT: the briefing includes a quantified sentiment block
  with a macro tilt in [-1, +1] and ticker-specific scores. Treat
  |macro_tilt| > 0.3 as a STRONG overnight driver. If macro_tilt is
  strongly negative (< -0.3), downgrade conviction one notch on BULLISH
  calls and favor HOLD. If ticker-specific tilt is strongly negative and
  recent (< 24h), prefer HOLD/AVOID even if momentum is positive.
- OVERNIGHT GLOBAL RISK: the briefing opens with an overnight-signals block
  (S&P 500, VIX, Hang Seng, Nikkei, EEM, DXY) and a rules-based GAP PRIOR.
  If GAP_DOWN + VIX stressed, downgrade conviction one notch (HIGH->MEDIUM,
  MEDIUM->LOW). If GAP_UP + VIX normal, you may keep conviction as-is.
  Frontier EM (which PSX is a member of) typically loses 1-2x of S&P moves
  on risk-off days.
- VIX-CONDITIONAL range: when VIX is elevated (>=18) or stressed (>=22),
  widen the expected_return_5d [low, high] band by at least 50% vs. a
  normal-VIX day. Predicting a tight band on a stressed-VIX day leads to
  systematic inside-range misses.
- Be skeptical. If nothing special is happening, say NEUTRAL/LOW/HOLD.

Return JSON ONLY. No other text."""


def build_briefing(ctx: dict) -> str:
    """Render the full_context dict as a clean text briefing for the LLM."""
    sym = ctx.get("symbol", "?")
    sector = ctx.get("sector", "?")
    price = ctx.get("price", {})
    tech = ctx.get("technical", {})
    signal = ctx.get("phase1_signal", {})
    news = ctx.get("news", {})
    fipi = ctx.get("fipi_flows", {})
    macro = ctx.get("macro", {})
    rate = ctx.get("policy_rate", {})

    def pct(v):
        return "N/A" if v is None else f"{v*100:+.2f}%"

    # Overnight global risk — uses price.as_of as the cutoff date
    cutoff = pd.Timestamp(price.get("as_of") or pd.Timestamp.today())
    overnight_block = build_overnight_block(cutoff)

    lines = [
        f"=== {sym}  ({sector})  as of {price.get('as_of', '?')} ===",
        "",
        overnight_block,
        "",
        "PRICE & TECHNICALS",
        f"  Close: {price.get('close_pkr', '?')} PKR",
        f"  Returns: 1d={pct(price.get('ret_1d'))}  5d={pct(price.get('ret_5d'))}  "
        f"21d={pct(price.get('ret_21d'))}  63d={pct(price.get('ret_63d'))}  "
        f"252d={pct(price.get('ret_252d'))}",
    ]
    if tech and "error" not in tech:
        mom = tech.get("momentum") or {}
        ma = tech.get("moving_averages") or {}
        vol = tech.get("volatility") or {}
        rng = tech.get("ranges") or {}
        lines.append(
            f"  Momentum (log): 20d={pct(mom.get('20d_log_ret'))}  "
            f"60d={pct(mom.get('60d_log_ret'))}  "
            f"150d={pct(mom.get('150d_log_ret'))}  "
            f"250d={pct(mom.get('250d_log_ret'))}"
        )
        lines.append(
            f"  MAs: sma20={ma.get('sma_20')}  sma50={ma.get('sma_50')}  "
            f"sma200={ma.get('sma_200')}  px_vs_sma200_pct={ma.get('px_vs_sma200_pct')}"
        )
        lines.append(
            f"  Vol: rvol20d_ann={vol.get('rvol_20d_ann')}  "
            f"rvol60d_ann={vol.get('rvol_60d_ann')}  regime={vol.get('rvol_regime')}  "
            f"RSI14: {tech.get('rsi_14')}  trend: {tech.get('trend')}"
        )
        lines.append(
            f"  52w range: {rng.get('low_52w')} – {rng.get('high_52w')}  "
            f"dist_from_high_pct={rng.get('dist_from_52w_high_pct')}  "
            f"dist_from_low_pct={rng.get('dist_from_52w_low_pct')}"
        )

    lines += [
        "",
        "MOMENTUM RANK IN 15-STOCK UNIVERSE",
        f"  Rank: {ctx.get('momentum_rank_today')} / 15",
        f"  In Phase-1 top-5 today: {ctx.get('in_phase1_top5')}",
        f"  Would-be top-5 if market filter off: {ctx.get('in_top5_if_filter_off')}",
        f"  Market filter regime: {signal.get('market_regime', '?')} "
        f"(risk_on={signal.get('market_risk_on', '?')})",
    ]

    lines += [
        "",
        "MACRO CONTEXT",
    ]
    for k, v in (macro.get("indicators") or {}).items():
        if "error" in v:
            continue
        lines.append(
            f"  {v.get('label', k)}: {v.get('value')}  "
            f"5d={pct(v.get('ret_5d'))}  21d={pct(v.get('ret_21d'))}  "
            f"63d={pct(v.get('ret_63d'))}"
        )
    if macro.get("narrative"):
        lines.append(f"  Narrative: {macro['narrative']}")

    lines += [
        "",
        "SBP POLICY RATE",
        f"  Rate: {rate.get('policy_rate_pct')}%  "
        f"Corridor: {rate.get('corridor')}",
    ]
    if rate.get("interpretation"):
        lines.append(f"  Regime: {rate['interpretation']}")

    lines += [
        "",
        "FIPI / LIPI FLOWS TODAY",
        f"  Foreign net: {fipi.get('foreign_net_pkr_mn')} mn PKR  "
        f"Local net: {fipi.get('local_net_pkr_mn')} mn PKR  "
        f"Regime: {fipi.get('foreign_regime')}",
    ]
    # Is the symbol's sector on the top flows list?
    for s in (fipi.get("top_sectors_by_flow") or []):
        name = (s.get("sector") or "").lower()
        if sector.lower().split()[0] in name or name in sector.lower():
            lines.append(
                f"  Sector flow match: '{s.get('sector')}' net_usd_mn={s.get('net_usd_mn')}"
            )

    lines += [
        "",
        f"NEWS MATCHED TO {sym} / SECTOR ({news.get('count', 0)} items)",
    ]
    for a in (news.get("articles") or [])[:6]:
        lines.append(
            f"  [{a.get('published_at', '?')}] ({a.get('source','?')[:18]}) "
            f"{a.get('title','')[:140]}"
        )

    # Scored news sentiment (quantified, weighted)
    try:
        sent_block = scored_sentiment_block(
            hours_macro=24.0, hours_ticker=72.0,
            symbols=[sym], top_headlines=5,
        )
        lines += ["", sent_block]
    except Exception as e:
        lines += ["", f"SCORED NEWS SENTIMENT: (skipped — {type(e).__name__})"]

    return "\n".join(lines)


# ==========================================================================
# LLM calls — Claude
# ==========================================================================
def predict_with_claude(briefing: str, sym: str, close: float) -> dict:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        system=PREDICTION_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Generate a 5-trading-day prediction for {sym}. "
                f"Current price: {close} PKR.\n\n"
                f"DATA BRIEFING:\n{briefing}\n\n"
                f"Return the JSON prediction now."
            ),
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text").strip()
    return parse_json_loose(text), text


def predict_with_gemini(briefing: str, sym: str, close: float) -> dict:
    from google import genai
    from google.genai import types as gtypes

    client = genai.Client(
        api_key=(os.environ.get("GOOGLE_API_KEY")
                 or os.environ.get("GEMINI_API_KEY")))
    config = gtypes.GenerateContentConfig(
        system_instruction=PREDICTION_SYSTEM_PROMPT,
        temperature=0.2,
        max_output_tokens=700,
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[gtypes.Content(
            role="user",
            parts=[gtypes.Part.from_text(text=(
                f"Generate a 5-trading-day prediction for {sym}. "
                f"Current price: {close} PKR.\n\n"
                f"DATA BRIEFING:\n{briefing}\n\n"
                f"Return the JSON prediction now."
            ))],
        )],
        config=config,
    )
    text = (getattr(resp, "text", "") or "").strip()
    return parse_json_loose(text), text


def parse_json_loose(text: str) -> dict:
    """Parse JSON from an LLM response, tolerating code fences + trailing text."""
    import re as _re
    t = text.strip()
    # Remove ```json ... ``` fences if present
    if t.startswith("```"):
        # drop first line
        t = t.split("\n", 1)[1] if "\n" in t else t
        # drop trailing fence
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    # JSON spec disallows leading '+' on numbers, but Claude sometimes emits
    # `"expected_return_5d_high_pct": +4.2`. Strip the '+' when it sits right
    # after `:` (or after `,` / `[` for arrays of numbers).
    t = _re.sub(r'([:\[,]\s*)\+(\d)', r'\1\2', t)
    # First try raw parse
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Find the first '{' and last '}' and parse between them
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except json.JSONDecodeError:
            pass
    # Last-resort: brace-balance walk from first '{', stopping at the matching '}'.
    # Handles cases where Claude appends prose after a complete JSON object, or
    # where the closing fence is malformed.
    if a != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(a, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(t[a:i + 1])
                        except json.JSONDecodeError:
                            break
    # On failure, dump the full raw response so we can diagnose.
    try:
        from pathlib import Path as _P
        _P("_bad_llm_response.txt").write_text(
            f"=== {datetime.now().isoformat()} ===\n{text}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    raise ValueError(
        f"Could not parse JSON from LLM response (full text saved to "
        f"_bad_llm_response.txt; first 200 chars): {text[:200]}"
    )


# ==========================================================================
# Rule-based fallback (no LLM)
# ==========================================================================
def predict_with_rules(ctx: dict) -> dict:
    """Deterministic prediction from the data bundle. No news sentiment."""
    price = ctx.get("price", {})
    tech = ctx.get("technical", {})
    sig = ctx.get("phase1_signal", {})
    fipi = ctx.get("fipi_flows", {})
    rate = ctx.get("policy_rate", {})
    macro = ctx.get("macro", {}).get("indicators", {})

    close = float(price.get("close_pkr") or 0)
    mom = (tech.get("momentum") or {}) if "error" not in tech else {}
    ma = (tech.get("moving_averages") or {}) if "error" not in tech else {}
    vol = (tech.get("volatility") or {}) if "error" not in tech else {}
    m63 = mom.get("60d_log_ret") or 0
    m150 = mom.get("150d_log_ret") or 0
    m20 = mom.get("20d_log_ret") or 0
    vol_ann = vol.get("rvol_20d_ann") or 0.30
    px_vs_200 = ma.get("px_vs_sma200_pct") or 0
    above_200 = px_vs_200 > 0
    in_top5 = bool(ctx.get("in_phase1_top5"))
    market_on = bool(sig.get("market_risk_on"))
    rsi = tech.get("rsi_14") if "error" not in tech else None

    # Score in [-1, +1]
    score = 0.0
    drivers: list[str] = []
    risks: list[str] = []

    # Momentum
    if m150 > 0.20:
        score += 0.40; drivers.append(f"Strong 150d momentum {m150*100:+.1f}%")
    elif m150 > 0.05:
        score += 0.20; drivers.append(f"Positive 150d momentum {m150*100:+.1f}%")
    elif m150 < -0.05:
        score -= 0.25; risks.append(f"Negative 150d momentum {m150*100:+.1f}%")

    if m63 > 0.10:
        score += 0.20; drivers.append(f"Strong 60d momentum {m63*100:+.1f}%")
    elif m63 < -0.05:
        score -= 0.15; risks.append(f"Weakening 60d momentum {m63*100:+.1f}%")

    # Short-term momentum (20d)
    if m20 > 0.08:
        score += 0.15; drivers.append(f"Strong 20d momentum {m20*100:+.1f}%")
    elif m20 < -0.05:
        score -= 0.10; risks.append(f"20d weakness {m20*100:+.1f}%")

    # Trend
    if above_200:
        score += 0.10; drivers.append(f"Price {px_vs_200:+.1f}% above 200-day SMA")
    else:
        score -= 0.15; risks.append(f"Price {px_vs_200:+.1f}% below 200-day SMA")

    # RSI extremes
    if rsi is not None:
        if rsi > 75:
            score -= 0.10; risks.append(f"RSI {rsi} — overbought, pullback risk")
        elif rsi < 30:
            score += 0.10; drivers.append(f"RSI {rsi} — oversold, bounce setup")

    # Strategy signal
    if in_top5:
        score += 0.25; drivers.append("In Phase-1 top-5 today")
    elif ctx.get("in_top5_if_filter_off"):
        drivers.append("Top-5 by momentum but market filter off")

    if not market_on:
        score -= 0.10
        risks.append("Market breadth weak — Phase-1 in cash")

    # FIPI
    f_net = fipi.get("foreign_net_pkr_mn") or 0
    if f_net > 500:
        score += 0.10; drivers.append(f"Foreign net buying +{f_net:.0f} mn PKR")
    elif f_net < -500:
        score -= 0.10; risks.append(f"Foreign net selling {f_net:+.0f} mn PKR")

    # Macro — Brent matters for E&P (OGDC, PPL)
    sector = ctx.get("sector", "")
    brent = macro.get("brent", {})
    b21 = brent.get("ret_21d") or 0
    if "Oil & Gas Exploration" in sector:
        if b21 > 0.05:
            score += 0.15; drivers.append(f"Brent +{b21*100:.1f}% in 21d "
                                          f"(E&P tailwind)")
        elif b21 < -0.05:
            score -= 0.15; risks.append(f"Brent {b21*100:+.1f}% in 21d "
                                        f"(E&P headwind)")
    if "Bank" in sector and rate.get("policy_rate_pct", 0) <= 11:
        drivers.append("Accommodative policy rate — banking margin risk but "
                       "volume growth tailwind")

    # Finalize
    score = max(-1.0, min(1.0, score))
    if score > 0.25:
        direction = "BULLISH"
    elif score < -0.25:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    abs_score = abs(score)
    if abs_score > 0.55:
        conviction = "HIGH"
    elif abs_score > 0.25:
        conviction = "MEDIUM"
    else:
        conviction = "LOW"

    # Expected return: anchor on direction + vol
    daily_vol = vol_ann / (252 ** 0.5)
    horizon_sigma = daily_vol * (HORIZON_DAYS ** 0.5) * 100  # %
    mid = score * horizon_sigma * 0.8  # damped
    low = mid - horizon_sigma
    high = mid + horizon_sigma

    # Action
    if in_top5 and direction == "BULLISH":
        action = "HOLD"
    elif ctx.get("in_top5_if_filter_off") and direction in ("BULLISH", "NEUTRAL"):
        action = "HOLD"
    elif direction == "BEARISH" and not in_top5:
        action = "TRIM" if above_200 else "AVOID"
    elif direction == "BULLISH" and not in_top5:
        action = "ADD"
    else:
        action = "HOLD"

    stop = round(close * 0.92, 2)
    target = round(close * (1 + max(high, 3.0) / 100.0), 2)

    rationale = (
        f"Rule-based score {score:+.2f} (direction {direction}, "
        f"conviction {conviction}). Driven by {len(drivers)} positive factors "
        f"and {len(risks)} risks. No news sentiment integrated "
        f"(LLM not available)."
    )

    return {
        "direction": direction,
        "conviction": conviction,
        "expected_return_5d_low_pct": round(low, 2),
        "expected_return_5d_mid_pct": round(mid, 2),
        "expected_return_5d_high_pct": round(high, 2),
        "suggested_action": action,
        "suggested_stop_pkr": stop,
        "suggested_target_pkr": target,
        "rationale": rationale,
        "key_drivers": drivers[:4] or ["No strong drivers"],
        "key_risks": risks[:4] or ["No material risks flagged"],
    }


# ==========================================================================
# Main
# ==========================================================================
def gather_snapshot(ctx: dict) -> dict:
    """Compact numeric snapshot saved with every prediction for later scoring."""
    price = ctx.get("price", {})
    tech = ctx.get("technical", {})
    mom = (tech.get("momentum") or {}) if "error" not in tech else {}
    ma = (tech.get("moving_averages") or {}) if "error" not in tech else {}
    vol = (tech.get("volatility") or {}) if "error" not in tech else {}
    fipi = ctx.get("fipi_flows", {})
    macro = (ctx.get("macro") or {}).get("indicators", {})
    rate = ctx.get("policy_rate", {})
    news = ctx.get("news", {})
    return {
        "as_of_price_date": price.get("as_of"),
        "close_pkr": price.get("close_pkr"),
        "ret_5d": price.get("ret_5d"),
        "ret_21d": price.get("ret_21d"),
        "ret_63d": price.get("ret_63d"),
        "mom_20d_log": mom.get("20d_log_ret"),
        "mom_60d_log": mom.get("60d_log_ret"),
        "mom_150d_log": mom.get("150d_log_ret"),
        "mom_250d_log": mom.get("250d_log_ret"),
        "rvol_20d_ann": vol.get("rvol_20d_ann"),
        "rvol_regime": vol.get("rvol_regime"),
        "rsi_14": tech.get("rsi_14") if "error" not in tech else None,
        "px_vs_sma200_pct": ma.get("px_vs_sma200_pct"),
        "trend": tech.get("trend") if "error" not in tech else None,
        "momentum_rank_today": ctx.get("momentum_rank_today"),
        "in_phase1_top5": ctx.get("in_phase1_top5"),
        "market_risk_on": (ctx.get("phase1_signal") or {}).get("market_risk_on"),
        "fipi_foreign_net_pkr_mn": fipi.get("foreign_net_pkr_mn"),
        "fipi_local_net_pkr_mn": fipi.get("local_net_pkr_mn"),
        "policy_rate_pct": rate.get("policy_rate_pct"),
        "brent_value": (macro.get("brent") or {}).get("value"),
        "brent_ret_21d": (macro.get("brent") or {}).get("ret_21d"),
        "usdpkr_value": (macro.get("usdpkr") or {}).get("value"),
        "usdpkr_ret_21d": (macro.get("usdpkr") or {}).get("ret_21d"),
        "news_count_matched": news.get("count"),
        "news_headlines": [a.get("title", "") for a in
                           (news.get("articles") or [])[:5]],
    }


def generate_one(sym: str, provider: str) -> dict:
    print(f"\n[{sym}] gathering context...", flush=True)
    ctx = get_full_context(sym)

    price = ctx.get("price", {})
    close = float(price.get("close_pkr") or 0)
    if close <= 0:
        print(f"  [{sym}] no price data, skipping")
        return {}

    briefing = build_briefing(ctx)
    raw_text = None
    try:
        if provider == "claude":
            pred, raw_text = predict_with_claude(briefing, sym, close)
            model = "claude-haiku-4-5"
        elif provider == "gemini":
            pred, raw_text = predict_with_gemini(briefing, sym, close)
            model = "gemini-2.5-flash"
        else:
            pred = predict_with_rules(ctx)
            model = "rule-based-v1"
    except Exception as e:
        print(f"  [{sym}] LLM call failed: {type(e).__name__}: {e} — "
              f"falling back to rules")
        pred = predict_with_rules(ctx)
        model = "rule-based-v1"

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    record = {
        "prediction_id": f"{date.today().isoformat()}-{sym}",
        "generated_at": now,
        "symbol": sym,
        "sector": ctx.get("sector"),
        "model": model,
        "horizon_trading_days": HORIZON_DAYS,
        "entry_price_pkr": close,
        **pred,
        "data_snapshot": gather_snapshot(ctx),
        "outcome": {
            "checked_at": None,
            "actual_end_price_pkr": None,
            "actual_return_pct": None,
            "direction_hit": None,
            "inside_range": None,
            "stop_triggered": None,
            "target_triggered": None,
        },
    }
    if raw_text:
        record["_llm_raw"] = raw_text[:1500]

    # Pretty print summary
    print(f"  [{sym}] {pred['direction']:>8s}  {pred['conviction']:>6s}  "
          f"mid={pred.get('expected_return_5d_mid_pct', '?')}%  "
          f"action={pred.get('suggested_action')}  "
          f"stop={pred.get('suggested_stop_pkr')}  "
          f"target={pred.get('suggested_target_pkr')}  "
          f"({model})", flush=True)
    return record


def load_log() -> dict:
    if LOG_PATH.exists():
        try:
            return json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"version": 1, "predictions": []}


def save_log(log: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(
        json.dumps(log, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["claude", "gemini", "rule", "auto"],
                        default="auto")
    parser.add_argument("--symbols", nargs="*",
                        help="Override default ticker list")
    args = parser.parse_args()

    provider = args.model if args.model != "auto" else detect_provider()
    print(f"Provider: {provider}")
    if provider == "rule":
        print("  (no API key found — using rule-based fallback)")

    tickers = args.symbols or list(REQUIRED_TICKERS)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Horizon: {HORIZON_DAYS} trading days")

    log = load_log()
    new_records = []
    for sym in tickers:
        start = time.time()
        rec = generate_one(sym, provider)
        if rec:
            new_records.append(rec)
        print(f"  (elapsed {time.time() - start:.1f}s)", flush=True)
        # Small sleep to be gentle on RSS / API rate limits
        time.sleep(0.5)

    # Deduplicate by prediction_id (same date + same ticker → overwrite)
    existing = {p["prediction_id"]: p for p in log["predictions"]}
    for r in new_records:
        existing[r["prediction_id"]] = r
    log["predictions"] = sorted(existing.values(), key=lambda p: p["prediction_id"])
    save_log(log)

    print(f"\nSaved {len(new_records)} predictions -> {LOG_PATH}")
    print(f"Total predictions in log: {len(log['predictions'])}")


if __name__ == "__main__":
    main()
