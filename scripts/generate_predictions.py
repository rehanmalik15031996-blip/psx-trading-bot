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
  "rationale": "<4-6 sentence paragraph; see 'RATIONALE REQUIREMENTS' below>",
  "key_drivers": ["<short bullet, each MUST cite one signal from the briefing>", ...],
  "key_risks": ["<short bullet, each MUST cite one signal from the briefing>", ...],
  "macro_tailwinds": ["<short bullet from MACRO IMPACT block, only those >=+1>", ...],
  "macro_headwinds": ["<short bullet from MACRO IMPACT block, only those <=-1>", ...]
}

RATIONALE REQUIREMENTS (analyst-mandatory — the investor needs to see the
"why" behind every bullish/bearish decision):
- The rationale MUST be 4-6 sentences. Length matters because the analyst
  has explicitly asked for explained decisions.
- Sentence 1: state the call (BULLISH/BEARISH/NEUTRAL) and the headline
  reason in plain English.
- Sentence 2: cite the dominant TECHNICAL signal (momentum, RSI,
  Bollinger, MACD, OBV).
- Sentence 3: cite the dominant FUNDAMENTAL or VALUE signal (P/E vs
  sector, fair value gap, quality, earnings momentum, management
  outlook).
- Sentence 4: cite the dominant MACRO / FLOWS signal (policy rate,
  Brent, USD/PKR, big-fish FIPI/LIPI, sector volume).
- Sentence 5 (optional): mention the most important RISK and what would
  flip the call.
- Use plain English. No jargon. The audience is a financial analyst,
  not a quant. Talk in cause-and-effect ("rates fell, so cement margins
  ease, so MLCF gains earnings power").

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
- INTRINSIC VALUE (slow signal, 6-24 month horizon): the briefing now
  includes a fair-value estimate vs current price. This is a SLOW signal
  and only marginally affects the 5-day call. Use it as follows:
    * BUY_VALUE with HIGH confidence + bullish momentum/flows: a small
      conviction upgrade is justified (LOW->MEDIUM, MEDIUM->HIGH).
    * SELL_VALUE with HIGH confidence + bearish momentum/flows: a small
      conviction upgrade on the bearish call is justified.
    * BUY_VALUE but bearish momentum: do NOT flip to BULLISH for 5 days;
      the value gap can stay open for 6+ months. Mention it as a 'longer-
      term tailwind' in the rationale and stay NEUTRAL/HOLD short-term.
    * SELL_VALUE but bullish momentum: a 5-day bullish call is still OK
      (momentum trumps value short-term), but downgrade conviction one
      notch (HIGH->MEDIUM) and flag the rich valuation as a key_risk.
    * NO_SIGNAL or LOW confidence: ignore the value layer for this stock.
- QUALITY SCORE (0-100, used as a multiplier on value):
    * HIGH (>=70) + BUY_VALUE = real edge — push conviction.
    * JUNK (<30) + BUY_VALUE = value trap — DOWNGRADE to HOLD even if
      momentum is positive. Flag "value trap risk" in key_risks.
    * HIGH + bullish momentum = strongest setup; conviction can be HIGH.
    * Quality alone is NOT a 5-day signal — only use it as a filter.
- EARNINGS MOMENTUM:
    * ACCELERATING + bullish momentum/news = HIGH conviction allowed.
    * RECOVERING (out of a slump) + positive news = MEDIUM-HIGH ok.
    * EROSION + neutral signals = downgrade to HOLD/AVOID even if
      technicals are fine (post-earnings drift is bearish).
    * DECELERATING = mild bearish bias.
- EARNINGS EVENT RISK (CRITICAL): if the briefing contains an
  "EARNINGS EVENT RISK (BLACKOUT)" line, you MUST set suggested_action
  to HOLD or AVOID and warn about post-result gap risk in the
  rationale, regardless of momentum / value signals. Earnings days
  produce 5-10% gaps that destroy short-term predictions. If it shows
  "EVENT WINDOW" (6-14 days out), you MAY still recommend BUY/ADD but
  with conviction one notch lower and tighter stop loss in the
  rationale.
- MANAGEMENT OUTLOOK (forward-looking, 1-12 month horizon): if the
  briefing contains a "MANAGEMENT OUTLOOK" stanza extracted from the
  Director's Report, treat it as a slow but high-signal layer:
    * Strongly bullish tone (>=+0.5) + HIGH guidance + bullish
      momentum: conviction can be HIGH; flag "management raising
      guidance" in rationale.
    * Bearish tone (<=-0.4): downgrade conviction one notch (HIGH
      ->MEDIUM, MEDIUM->LOW). Mention specific risks in key_risks.
    * Capex/expansion announced + bullish momentum: small upgrade in
      conviction is justified. BUT — if installed-vs-actual capacity
      utilisation is LOW (<70%) AND capex is announced, downgrade
      conviction one notch: capacity is the constraint, not demand.
    * Stale (>270d) or LOW guidance: ignore — narrative is too old.
- BOLLINGER BANDS (analyst-requested signal):
    * %B > 0.95 AND RSI > 70 → overbought stretch; downgrade BUY
      conviction one notch and tighten stop.
    * %B < 0.05 AND positive earnings momentum → mean-reversion
      BUY setup; conviction MEDIUM is justified.
    * width_pctile_252d <= 10 (a "squeeze") → coming move likely
      large in EITHER direction; widen the
      expected_return_5d [low, high] band by 50% relative to a normal
      day, regardless of direction.
- MACD: a fresh bullish histogram cross (days_since_cross <= 3)
  while close is above sma_50 = trend confirmation; supports HIGH
  conviction on a BULLISH call. A fresh bearish cross is the
  opposite signal.
- OBV: |5d change| > 5% in the direction of the price move = volume
  CONFIRMS the trend (good). Price up + OBV down (or vice versa) is
  a "non-confirmation" — downgrade conviction one notch.
- FUNDAMENTAL RATIOS vs sector medians:
    * P/E and P/B both > 30% above sector median + neutral momentum
      → stock is rich; favour HOLD/NEUTRAL.
    * P/E and P/B both > 30% below sector median + ACCELERATING
      earnings momentum + HIGH quality → strong "cheap quality"
      setup; conviction can be HIGH on a BULLISH call.
    * Dividend yield > 8% and payout_ratio > 100% → dividend may be
      unsustainable; flag as a key_risk.
- BIG FISH FLOWS (FIPI/LIPI institutional cohort):
    * "BIG FISH" = foreign + banks + mutual funds + insurance.
      This is the cohort that sets multi-day trend; retail
      (Individuals + Brokers) typically chases.
    * big_fish_net_pkr_mn strongly POSITIVE (>+150 mn) on a BULLISH
      call → conviction can be HIGH; institutional buying confirms.
    * big_fish_net_pkr_mn strongly NEGATIVE (<-150 mn) on a BULLISH
      call → downgrade conviction one notch and add "institutional
      selling against the move" as a key_risk.
    * If "this stock's sector is trading HOT" appears in the
      briefing, treat that sector as having short-term momentum
      tailwind — supports BULLISH calls on member stocks for ~3
      trading days.
- SECTOR-AWARE MACRO IMPACT (analyst-mandatory):
    * The briefing now contains a "MACRO IMPACT FOR THIS STOCK" stanza
      that lists today's active macro drivers, the sector verdict, and
      the stock-level verdict (with leverage / tier amplifiers).
    * Stock-level verdict STRONG TAILWIND (score >= +3) on a BULLISH
      call → conviction can be HIGH; mention the dominant driver in
      the rationale.
    * Stock-level verdict STRONG HEADWIND (score <= -3) on a BULLISH
      call → downgrade conviction one notch and add the headwind to
      key_risks.
    * Stock-level verdict TAILWIND (+1, +2): mention as a supporting
      reason in macro_tailwinds[].
    * Stock-level verdict HEADWIND (-1, -2): mention in
      macro_headwinds[].
    * If the stock is in a HEADWIND sector but you're calling BULLISH
      because of a stock-specific catalyst (earnings beat, Material
      Information positive), you MUST acknowledge the macro headwind in
      the rationale and explain why the stock-specific catalyst trumps
      it. NEVER ignore a STRONG HEADWIND silently.
    * Examples (the analyst asked for these explicitly):
        - Rate +1.0pp + Banking + tier-1 (MCB/MEBL): STRONG TAILWIND
          (+3 to +4) — NIM expansion. Push conviction.
        - Rate +1.0pp + Cement + high-D/E (e.g. MLCF if D/E>1):
          STRONG HEADWIND (-4 to -5). Downgrade to AVOID/SELL.
        - Brent +12% in 21d + Oil & Gas E&P: STRONG TAILWIND (+3) —
          revenue lift on every barrel.
        - PKR weak 3% in 21d + Pharma: STRONG HEADWIND (-2 to -3) —
          API import cost spike.
- MATERIAL INFORMATION (price-sensitive corporate disclosures):
    * If the briefing lists Material Information filings <= 2
      trading days old, widen the expected_return_5d band by 50%
      and downgrade BUY conviction one notch UNLESS the headline
      is unambiguously positive (e.g. "wins large export
      contract", "regulatory approval received").
    * Material Information ages quickly — anything older than 5
      trading days is information already in the price.
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
        # Bollinger Bands — analyst-flagged as a profitable indicator
        bb = tech.get("bollinger") or {}
        if bb.get("pctb") is not None:
            pctb = bb["pctb"]
            wpct = bb.get("width_pct")
            wpctile = bb.get("width_pctile_252d")
            state = bb.get("state") or "neutral"
            interp_bits = []
            if state == "near_upper_band":
                interp_bits.append("close near UPPER band — overbought / breakout")
            elif state == "near_lower_band":
                interp_bits.append("close near LOWER band — oversold / mean-revert")
            elif state == "squeeze":
                interp_bits.append("SQUEEZE — width in bottom decile, "
                                   "expansion likely")
            interp = (" — " + "; ".join(interp_bits)) if interp_bits else ""
            lines.append(
                f"  Bollinger %B={pctb:.2f}  width_pct={wpct}  "
                f"width_pctile_252d={wpctile}{interp}"
            )
        # MACD with cross detection
        macd = tech.get("macd") or {}
        if macd.get("histogram") is not None:
            hist = macd["histogram"]
            days = macd.get("days_since_cross")
            cross_dir = ("bullish" if hist > 0 else "bearish") if hist else "flat"
            days_str = f"{days}d ago" if days is not None else "n/a"
            lines.append(
                f"  MACD: line={macd.get('line')}  signal={macd.get('signal')}  "
                f"hist={hist:+.3f}  last_cross={cross_dir} ({days_str})"
            )
        # OBV (volume confirms / contradicts the move)
        obv = tech.get("obv") or {}
        if obv.get("change_5d_pct") is not None:
            ch = obv["change_5d_pct"]
            tag = ("volume CONFIRMS uptrend" if ch > 5
                   else "volume CONTRADICTS uptrend" if ch < -5
                   else "volume neutral")
            lines.append(f"  OBV 5d change: {ch:+.1f}%  ({tag})")
        # Stoch RSI
        if tech.get("stoch_rsi") is not None:
            sr = tech["stoch_rsi"]
            tag = ("overbought" if sr > 0.8
                   else "oversold" if sr < 0.2 else "mid-range")
            lines.append(f"  Stoch RSI: {sr:.2f}  ({tag})")

    # Fundamental ratios with sector comparison (analyst-requested, Layer 4)
    try:
        from connectors.yfinance_fundamentals import load_latest as _load_fund
        from brain.sector_ratios import load_sector_medians as _load_sec
        f = _load_fund(sym) or {}
        sec_med_payload = _load_sec()
        sec_med = (sec_med_payload.get("by_sector") or {}).get(sector, {})
        if any(f.get(k) is not None for k in
               ("pe_ratio", "pb_ratio", "dividend_yield_pct",
                "payout_ratio_pct")):
            def _vs(val, med, lower_is_cheap=True):
                if val is None or med in (None, 0):
                    return ""
                diff_pct = (val / med - 1.0) * 100.0
                tag = ("cheap vs sector" if (diff_pct < 0 and lower_is_cheap)
                       else "rich vs sector" if (diff_pct > 0 and lower_is_cheap)
                       else "above sector" if diff_pct > 0
                       else "below sector")
                return f" (vs sector median {med}, {diff_pct:+.0f}% — {tag})"
            lines += [
                "",
                f"FUNDAMENTAL RATIOS  ({sector})",
                f"  P/E ratio:  {f.get('pe_ratio')}"
                f"{_vs(f.get('pe_ratio'), sec_med.get('pe_med'))}",
                f"  P/B ratio:  {f.get('pb_ratio')}"
                f"{_vs(f.get('pb_ratio'), sec_med.get('pb_med'))}",
                f"  Dividend yield: {f.get('dividend_yield_pct')}%"
                f"{_vs(f.get('dividend_yield_pct'), sec_med.get('yield_med'), lower_is_cheap=False)}",
                f"  Payout ratio:   {f.get('payout_ratio_pct')}%  "
                f"(EPS distributed as dividend; >100% means dipping into reserves)",
            ]
            if sec_med.get("n"):
                lines.append(
                    f"  Sector sample size: n={sec_med['n']} "
                    f"({', '.join(sec_med.get('members', [])[:8])})"
                )
    except Exception as e:
        lines += ["", f"FUNDAMENTAL RATIOS: (skipped — {type(e).__name__})"]

    try:
        from config.universe import symbols as _univ_symbols
        _n_universe = len(_univ_symbols())
    except Exception:
        _n_universe = 35
    lines += [
        "",
        f"MOMENTUM RANK IN {_n_universe}-STOCK UNIVERSE",
        f"  Rank: {ctx.get('momentum_rank_today')} / {_n_universe}",
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

    # Sector-aware macro impact — translates the macro context above
    # into per-sector tailwinds / headwinds and per-stock amplifiers
    # (high-D/E names amplify rate moves, etc.). The analyst asked
    # explicitly: "interest rates increased by 1% — explain how this
    # impacts banking, cement, etc."  This stanza answers that.
    try:
        from brain.macro_impact import compute_macro_impact
        mi = compute_macro_impact(macro=macro, rate=rate, universe=[sym])
        drivers = mi.get("drivers") or []
        sym_block = (mi.get("by_symbol") or {}).get(sym) or {}
        sector_block = (mi.get("by_sector") or {}).get(sector) or {}
        if drivers or sym_block:
            lines += ["", "MACRO IMPACT FOR THIS STOCK"]
            kpis = mi.get("kpis") or {}
            if kpis:
                lines.append("  Industry KPIs:")
                if kpis.get("tbill_3m_pct") is not None:
                    chg = kpis.get("tbill_3m_change_5d")
                    lines.append(
                        f"    T-bill 3M: {kpis['tbill_3m_pct']:.2f}%"
                        + (f"  ({chg*100:+.0f} bps in 5d)" if chg is not None else ""))
                if kpis.get("kibor_3m_pct") is not None:
                    chg = kpis.get("kibor_3m_change_5d")
                    lines.append(
                        f"    KIBOR 3M: {kpis['kibor_3m_pct']:.2f}%"
                        + (f"  ({chg*100:+.0f} bps in 5d)" if chg is not None else ""))
                if kpis.get("reserves_sbp_usd_mn") is not None:
                    chg = kpis.get("reserves_change_30d")
                    lines.append(
                        f"    SBP reserves: USD {kpis['reserves_sbp_usd_mn']/1000:.1f} bn"
                        + (f"  ({chg/1000:+.1f} bn in 30d)" if chg is not None else ""))
                if kpis.get("kse100_close") is not None:
                    r5 = kpis.get("kse100_ret_5d"); r21 = kpis.get("kse100_ret_21d")
                    extras = []
                    if r5  is not None: extras.append(f"{r5*100:+.1f}% 5d")
                    if r21 is not None: extras.append(f"{r21*100:+.1f}% 21d")
                    extra_str = f"  ({', '.join(extras)})" if extras else ""
                    lines.append(
                        f"    KSE-100: {kpis['kse100_close']:,.0f}{extra_str}")
                if kpis.get("cpi_yoy_pct") is not None:
                    period = kpis.get("cpi_period") or ""
                    lines.append(
                        f"    CPI YoY: {kpis['cpi_yoy_pct']:.1f}%"
                        + (f"  ({period} print)" if period else ""))
            if drivers:
                lines.append(f"  Active drivers ({len(drivers)}):")
                for d in drivers[:8]:
                    lines.append(
                        f"    - {d.get('magnitude'):>8}  "
                        f"{d.get('name')}: {d.get('move')}"
                    )
            if sector_block:
                v = sector_block.get("verdict") or "NEUTRAL"
                sc = sector_block.get("score", 0)
                lines.append(
                    f"  Sector ({sector}) verdict: {v}  (score = {sc:+d})"
                )
                for t in (sector_block.get("tailwinds") or [])[:4]:
                    lines.append(f"    + {t}")
                for h in (sector_block.get("headwinds") or [])[:4]:
                    lines.append(f"    - {h}")
            if sym_block:
                lines.append(
                    f"  Stock-level verdict: {sym_block.get('verdict')}  "
                    f"(stock_score = {sym_block.get('stock_score'):+d}, "
                    f"sector_score = {sym_block.get('sector_score'):+d})"
                )
                if sym_block.get("amplifier_note"):
                    lines.append(f"    amplifier: {sym_block['amplifier_note']}")
            lines.append(
                "  Note: macro impact is a DETERMINISTIC sector rule book. "
                "Use it to *justify* your direction call in the rationale; "
                "high-magnitude tailwinds or headwinds should also adjust "
                "conviction up or down."
            )
    except Exception as e:
        lines += ["", f"MACRO IMPACT: (skipped — {type(e).__name__}: {e})"]

    lines += [
        "",
        "FIPI / LIPI FLOWS TODAY",
        f"  Foreign net: {fipi.get('foreign_net_pkr_mn')} mn PKR  "
        f"Local net: {fipi.get('local_net_pkr_mn')} mn PKR  "
        f"Regime: {fipi.get('foreign_regime')}",
    ]
    # Big-fish breakdown — analyst-flagged as the most informative read.
    bf_net = fipi.get("big_fish_net_pkr_mn")
    bf_components = fipi.get("big_fish_components") or []
    if bf_net is not None and bf_components:
        comp_str = ", ".join(
            f"{c.get('category')} "
            f"{(c.get('net_pkr_mn') or 0.0):+.1f}"
            for c in bf_components[:6]
        )
        lines.append(
            f"  BIG FISH (foreign + banks + mutual funds + insurance): "
            f"net = {bf_net:+.1f} mn PKR  "
            f"({fipi.get('big_fish_regime')})"
        )
        lines.append(f"    breakdown: {comp_str}")
        lines.append(
            f"  retail_net = {fipi.get('retail_net_pkr_mn')} mn PKR  "
            f"(Individuals/Brokers — typically chase, less informative)"
        )
    # Is the symbol's sector on the top flows list?
    for s in (fipi.get("top_sectors_by_flow") or []):
        name = (s.get("sector") or "").lower()
        if sector.lower().split()[0] in name or name in sector.lower():
            lines.append(
                f"  Sector flow match: '{s.get('sector')}' net_usd_mn={s.get('net_usd_mn')}"
            )

    # Sector volume heatmap — "where the action is" today vs 20-day avg
    try:
        from ui.tools import get_sector_volume_heatmap
        heat = get_sector_volume_heatmap(top_k=5, lookback_days=20)
        top = heat.get("top") or []
        if top:
            lines.append("  SECTOR VOLUME LEADERS TODAY (vs 20d avg):")
            for s in top:
                ratio = s.get("ratio_vs_avg")
                ratio_str = (f"{ratio:.1f}× avg" if ratio is not None
                             else "n/a")
                hot = " HOT" if s.get("is_hot") else ""
                lines.append(
                    f"    - {s.get('sector')}: "
                    f"PKR {s.get('today_pkr_mn')} mn  ({ratio_str}){hot}"
                )
            sector_first = sector.lower().split()[0]
            for s in top:
                sname = (s.get("sector") or "").lower()
                if sector_first in sname and s.get("is_hot"):
                    lines.append(
                        f"    NOTE: {sym}'s sector ({s['sector']}) is "
                        f"trading HOT today ({s.get('ratio_vs_avg')}× "
                        f"normal volume) — possible institutional "
                        f"rotation."
                    )
                    break
    except Exception as e:
        lines.append(f"  SECTOR VOLUME HEATMAP: (skipped — "
                     f"{type(e).__name__})")

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

    # Fundamental fair value (slow / 6-24 month signal)
    try:
        from brain.valuation import value_signal
        v = value_signal(sym)
        if "error" in v:
            lines += ["",
                      f"INTRINSIC VALUE: (no fundamentals — {v['error'][:80]})"]
        else:
            warn_str = (" | warnings: " + "; ".join(v.get("warnings", []))
                        if v.get("warnings") else "")
            lines += [
                "",
                "INTRINSIC VALUE (fair-value vs market price)",
                f"  fair_value     = {v.get('fair_value')} PKR",
                f"  current_price  = {v.get('current_price')} PKR",
                f"  upside_pct     = {v.get('upside_pct')}%   "
                f"signal={v.get('signal')}   confidence={v.get('confidence')}",
                f"  method         = {v.get('method')}{warn_str}",
                f"  (Slow 6-24m signal. BUY_VALUE = ≥25% upside; "
                f"SELL_VALUE = ≤-10% upside.)",
            ]
    except Exception as e:
        lines += ["", f"INTRINSIC VALUE: (skipped — {type(e).__name__})"]

    # Quality score (filter for value traps)
    try:
        from brain.quality import quality_score
        q = quality_score(sym)
        if q.get("error"):
            lines += ["", f"QUALITY: (skipped — {q['error'][:80]})"]
        else:
            comps = q.get("components", {})
            roe = comps.get("profitability", {}).get("value")
            de = comps.get("leverage", {}).get("value")
            cv = comps.get("stability", {}).get("value")
            lines += [
                "",
                f"QUALITY SCORE: {q.get('quality_score')}/100  band={q.get('band')}",
                f"  ROE={roe}%   D/E={de}   EPS_5y_CV={cv}",
                f"  (HIGH+BUY_VALUE = real edge; JUNK+BUY_VALUE = trap; "
                f"HIGH+momentum = strongest signal.)",
            ]
    except Exception as e:
        lines += ["", f"QUALITY: (skipped — {type(e).__name__})"]

    # Earnings momentum (post-earnings drift)
    try:
        from brain.quality import earnings_momentum
        em = earnings_momentum(sym)
        if em.get("flag") not in (None, "INSUFFICIENT_DATA"):
            lines += [
                "",
                f"EARNINGS MOMENTUM: {em['flag']}   "
                f"YoY={em.get('yoy_growth_pct')}%  "
                f"prior_YoY={em.get('prior_yoy_growth_pct')}%  "
                f"accel={em.get('acceleration_pp')}pp  "
                f"3y_CAGR={em.get('cagr_3y_pct')}%",
            ]
    except Exception as e:
        lines += ["", f"EARNINGS MOMENTUM: (skipped — {type(e).__name__})"]

    # Earnings event risk
    try:
        from brain.earnings_calendar import next_event
        ev = next_event(sym)
        if ev.get("days_until") is not None and 0 <= ev["days_until"] <= 14:
            tag = ("BLACKOUT" if ev.get("in_blackout_5d") else
                   "EVENT WINDOW")
            lines += [
                "",
                f"EARNINGS EVENT RISK ({tag}): {ev['symbol']} likely "
                f"reports on {ev['next_event_date_utc']} (in "
                f"{ev['days_until']} days, conf={ev['confidence']}, "
                f"src={ev['source']}). "
                + ("Do NOT recommend BUY/ADD; expect post-result gap."
                   if ev.get("in_blackout_5d")
                   else "Flag in rationale; consider tighter stop.")
            ]
    except Exception as e:
        lines += ["", f"EARNINGS EVENT RISK: (skipped — {type(e).__name__})"]

    # Management outlook — extracted from latest Director's Report
    try:
        from ui import dashboard_data as _dash
        hist = _dash.management_outlook_history(sym)
        if hist:
            mo = hist[0]
            try:
                fdate = pd.Timestamp(mo.get("filing_date"))
                age_days = (pd.Timestamp(price.get("as_of") or
                                            pd.Timestamp.today()) -
                              fdate).days
            except Exception:
                age_days = None
            stale_tag = ""
            if age_days is not None:
                if age_days > 270:
                    stale_tag = "  (STALE — narrative >9 months old)"
                elif age_days <= 14:
                    stale_tag = "  (FRESH — filed within 2 weeks)"
            tone = float(mo.get("outlook_tone") or 0.0)
            plans = mo.get("growth_plans") or []
            risks = mo.get("risks_mentioned") or []
            flags = []
            if mo.get("capex_announced"):     flags.append("CAPEX")
            if mo.get("expansion_announced"): flags.append("EXPANSION")
            lines += [
                "",
                f"MANAGEMENT OUTLOOK (Director's Report — "
                f"{mo.get('fy_period') or mo.get('doc_type')} filed "
                f"{mo.get('filing_date')}){stale_tag}",
                f"  tone = {tone:+.2f}   guidance = "
                f"{mo.get('guidance_strength') or 'LOW'}   "
                f"flags = {', '.join(flags) if flags else '—'}",
                f"  outlook: {(mo.get('outlook_summary') or '')[:280]}",
            ]
            # Capacity utilisation — analyst-flagged: a low utilisation +
            # capex announcement means demand isn't actually the binding
            # constraint, so the LLM gets an explicit gating signal here.
            inst_cap = mo.get("installed_capacity")
            act_prod = mo.get("actual_production")
            util_pct = mo.get("capacity_utilization_pct")
            if inst_cap or act_prod or util_pct is not None:
                cap_bits = []
                if inst_cap:
                    cap_bits.append(f"installed={str(inst_cap)[:60]}")
                if act_prod:
                    cap_bits.append(f"actual={str(act_prod)[:60]}")
                if util_pct is not None:
                    cap_bits.append(f"utilization={util_pct:.0f}%")
                tag = ""
                if util_pct is not None:
                    if util_pct < 70 and mo.get("capex_announced"):
                        tag = ("  (LOW UTILISATION + CAPEX — capacity is "
                               "NOT the binding constraint; downgrade "
                               "conviction one notch)")
                    elif util_pct >= 90 and mo.get("expansion_announced"):
                        tag = ("  (HIGH UTILISATION + EXPANSION — "
                               "expansion is well-justified by demand)")
                lines.append("  capacity: " + " | ".join(cap_bits) + tag)
            if mo.get("new_products"):
                lines.append(
                    "  new products: "
                    + "; ".join(str(x)[:80] for x in mo["new_products"][:3])
                )
            if plans:
                lines.append(
                    "  top plan: " + (plans[0] or "")[:200]
                )
            if risks:
                lines.append(
                    "  top risk: " + (risks[0] or "")[:200]
                )
        else:
            lines += ["",
                       "MANAGEMENT OUTLOOK: no Director's Report cached "
                       "for this symbol yet."]
    except Exception as e:
        lines += ["",
                   f"MANAGEMENT OUTLOOK: (skipped — {type(e).__name__})"]

    # Material Information — high-volatility flag (analyst-requested)
    try:
        from ui import dashboard_data as _dash2
        mi = _dash2.material_information_recent(symbol=sym, days=10,
                                                  top_k=5)
        rows = (mi or {}).get("rows") or []
        if rows:
            lines += [
                "",
                f"MATERIAL INFORMATION (last 10 trading days, {len(rows)} filings)",
            ]
            for r in rows[:5]:
                title = (r.get("title") or "")[:130]
                lines.append(f"  [{r.get('date')}] {title}")
            lines.append(
                "  Note: Material Information disclosures typically "
                "precede 3-7% gaps. Treat as a VOLATILITY FLAG: widen "
                "the expected_return_5d band by 50% if any filing is "
                "<= 2 trading days old, and downgrade BUY conviction "
                "one notch unless the news is unambiguously positive."
            )
        else:
            lines += ["", "MATERIAL INFORMATION: none in the last "
                       "10 days."]
    except Exception as e:
        lines += ["", f"MATERIAL INFORMATION: (skipped — "
                   f"{type(e).__name__})"]

    return "\n".join(lines)


# ==========================================================================
# LLM calls — Claude
# ==========================================================================
def predict_with_claude(briefing: str, sym: str, close: float) -> dict:
    """Per-ticker 5-day prediction.

    Default model upgraded 2026-05-01 from Haiku 4.5 → Sonnet 4.5 with
    a small 2k-token extended-thinking budget. Sonnet on a per-ticker
    briefing produces noticeably better-calibrated price ranges and
    catches macro / news contradictions Haiku missed (e.g. "BUY on
    HUBC because momentum is +6%" while ignoring a circular-debt
    headline). Set ``PSX_PREDICT_MODEL`` to override (e.g.
    ``claude-haiku-4-5`` for cheap test runs); set
    ``PSX_PREDICT_THINKING_BUDGET=0`` to disable extended thinking.
    """
    import httpx
    from anthropic import Anthropic
    # Hard 90-second read timeout per request.  Without this, a single
    # stalled API call will block the entire 35-symbol loop until
    # GitHub Actions kills the job at the workflow timeout limit.
    # The connect timeout is kept short (10s) because a connection that
    # takes >10s is almost certainly a network issue, not slow inference.
    _http = httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0))
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                       http_client=_http)
    model = os.environ.get("PSX_PREDICT_MODEL", "claude-sonnet-4-5")
    try:
        budget = int(os.environ.get("PSX_PREDICT_THINKING_BUDGET", "2000"))
    except ValueError:
        budget = 2000
    kwargs: dict = {}
    max_tokens = 1500
    if budget >= 1024 and model.startswith(("claude-sonnet-4", "claude-opus-4")):
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        kwargs["temperature"] = 1.0
        max_tokens = max(max_tokens, budget + 1024)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
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
        **kwargs,
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
def predict_with_rules(ctx: dict, playbook_bias: dict | None = None) -> dict:
    """Deterministic prediction from the data bundle. No news sentiment.

    `playbook_bias` is optional; when provided it should be the dict produced
    by `brain.strategist_overlays.compute_predictor_bias(briefing)`. It
    contains per-sector and per-symbol score deltas derived from fired
    playbook cases — applied AFTER the technical signal so they can tilt
    the prediction direction (e.g. when `imf_review_mission_week` fires,
    Banking sector gets a -0.15 score bias which can flip a NEUTRAL HOLD
    into a BEARISH AVOID for state-owned banks during pre-IMF weeks).
    """
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

    # ----------------------------------------------------------------- playbook
    # Apply playbook-driven sector + symbol bias deltas. Built from
    # briefing.playbook_analogues + reactions block in cases.json. This is
    # the layer that catches "all banks should be biased BEARISH because
    # IMF mission is active this week" — exactly the gap that let
    # 14 of 35 names get held through -3% sector moves on May 11-13.
    if playbook_bias:
        sym_now = ctx.get("symbol")
        sec_bias = (playbook_bias.get("sector_bias") or {}).get(sector, 0.0)
        if sec_bias:
            score += sec_bias
            tag = (f"Playbook overlay (sector {sector}): "
                   f"{sec_bias:+.2f} score bias from "
                   f"{playbook_bias.get('fired_case_ids', [])[:3]}")
            (drivers if sec_bias > 0 else risks).append(tag)
        sym_bias = (playbook_bias.get("symbol_bias") or {}).get(sym_now, 0.0)
        if sym_bias:
            score += sym_bias
            tag = (f"Playbook overlay (symbol {sym_now}): "
                   f"{sym_bias:+.2f} score bias")
            (drivers if sym_bias > 0 else risks).append(tag)

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

    # Sector-aware macro impact (so the rule-based fallback is not blind
    # to the analyst's macro requirement).
    macro_tailwinds: list[str] = []
    macro_headwinds: list[str] = []
    macro_summary = ""
    try:
        from brain.macro_impact import compute_macro_impact
        sym_for_mi = ctx.get("symbol", "?")
        mi = compute_macro_impact(macro={"indicators": macro}, rate=rate,
                                    universe=[sym_for_mi])
        sym_block = (mi.get("by_symbol") or {}).get(sym_for_mi) or {}
        for t in (sym_block.get("tailwinds") or [])[:3]:
            macro_tailwinds.append(t)
        for h in (sym_block.get("headwinds") or [])[:3]:
            macro_headwinds.append(h)
        if sym_block.get("verdict"):
            macro_summary = (f"Macro verdict: {sym_block.get('verdict')} "
                              f"(stock score {sym_block.get('stock_score'):+d}). ")
    except Exception:
        pass

    rationale = (
        f"Rule-based call: {direction} with {conviction} conviction "
        f"(score {score:+.2f}). "
        f"Drivers ({len(drivers)}): "
        f"{drivers[0] if drivers else 'none material'}. "
        f"Risks ({len(risks)}): "
        f"{risks[0] if risks else 'none material'}. "
        f"{macro_summary}"
        f"This is a deterministic fallback — the AI model was "
        f"unavailable; rationale is therefore terse."
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
        "macro_tailwinds": macro_tailwinds,
        "macro_headwinds": macro_headwinds,
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


def generate_one(sym: str, provider: str,
                 playbook_bias: dict | None = None) -> dict:
    print(f"\n[{sym}] gathering context...", flush=True)
    ctx = get_full_context(sym)
    # Make sym available to per-stock helpers (predictor reads it for the
    # playbook symbol_bias lookup).
    ctx.setdefault("symbol", sym)

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
            model = os.environ.get("PSX_PREDICT_MODEL", "claude-sonnet-4-5")
        elif provider == "gemini":
            pred, raw_text = predict_with_gemini(briefing, sym, close)
            model = "gemini-2.5-flash"
        else:
            pred = predict_with_rules(ctx, playbook_bias=playbook_bias)
            model = "rule-based-v1"
    except Exception as e:
        print(f"  [{sym}] LLM call failed: {type(e).__name__}: {e} — "
              f"falling back to rules")
        pred = predict_with_rules(ctx, playbook_bias=playbook_bias)
        model = "rule-based-v1"

    # Critic self-review: deterministic post-checks that catch the
    # gross logic errors the LLM occasionally makes (BULLISH call with
    # bearish drivers, stop/target geometry inverted, sharp
    # disagreement with the seven-lens synthesizer). Severity 'fail'
    # forces HOLD; 'warn' downgrades conviction one notch. Every
    # decision is stamped on `critic_notes` so the analyst can audit.
    try:
        from brain.prediction_critic import review as _critic_review
        _critic_review(pred, sym, close)
    except Exception as e:
        print(f"  [{sym}] critic self-review failed: "
              f"{type(e).__name__}: {e}")

    # Snapshot the deterministic macro-impact reading at prediction
    # time, so the UI ("Why this call?") can show the same tailwinds /
    # headwinds the LLM saw, even if macro data shifts later.
    macro_impact_snapshot = None
    try:
        from brain.macro_impact import compute_macro_impact
        mi_full = compute_macro_impact(
            macro=ctx.get("macro"), rate=ctx.get("policy_rate"),
            universe=[sym],
        )
        macro_impact_snapshot = {
            "drivers": mi_full.get("drivers") or [],
            "by_sector": (mi_full.get("by_sector") or {}).get(
                ctx.get("sector"), {}),
            "by_symbol": (mi_full.get("by_symbol") or {}).get(sym, {}),
            "kpis": mi_full.get("kpis") or {},
        }
    except Exception:
        macro_impact_snapshot = None

    # Pre-MPC alert: if the SBP MPC meets in <= PRE_WINDOW_DAYS and the
    # stock's sector is rate-sensitive, downgrade conviction one notch
    # so a pre-meeting position is not built with full size. Also stamp
    # a flag so the UI can show "MPC cap applied" to the analyst.
    mpc_state = (macro_impact_snapshot or {}).get("mpc_alert") or {}
    if not mpc_state:
        try:
            from config.sbp_mpc_calendar import mpc_alert_state
            mpc_state = mpc_alert_state()
        except Exception:
            mpc_state = {}
    mpc_cap_applied = False
    if (mpc_state.get("in_pre_window")
            and ctx.get("sector") in (mpc_state.get(
                "rate_sensitive_sectors") or [])):
        original_conviction = pred.get("conviction")
        downgrade = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}
        new_conviction = downgrade.get(original_conviction,
                                          original_conviction)
        if new_conviction != original_conviction:
            pred["conviction"] = new_conviction
            mpc_cap_applied = True
            # Append to risks so the rationale stays auditable.
            risks = list(pred.get("key_risks") or [])
            risks.append(
                f"SBP MPC in {mpc_state.get('days_until')} day(s) on "
                f"{mpc_state.get('next_mpc')} — sector is rate-"
                f"sensitive; conviction capped from "
                f"{original_conviction} to {new_conviction} pending "
                f"the rate decision."
            )
            pred["key_risks"] = risks[:6]

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
        "macro_impact": macro_impact_snapshot,
        "mpc_alert": mpc_state,
        "mpc_cap_applied": mpc_cap_applied,
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

    if args.symbols:
        tickers = list(args.symbols)
    else:
        # Default to the full 15-stock universe — generating predictions
        # only for the 6 REQUIRED_TICKERS leaves 9 of the user's tracked
        # names without a daily forecast.
        try:
            from config.universe import UNIVERSE
            tickers = [u.symbol for u in UNIVERSE]
        except Exception:
            tickers = list(REQUIRED_TICKERS)
    print(f"Tickers ({len(tickers)}): {', '.join(tickers)}")
    print(f"Horizon: {HORIZON_DAYS} trading days")

    # Compute playbook bias ONCE per session (heavy briefing call). Passed
    # to every per-stock predictor so the rule-based path picks up the
    # session's fired playbook overlays (e.g. when imf_review_mission_week
    # fires, banks get -0.15 score bias making them more likely to land
    # BEARISH/AVOID even from a flat technical signal).
    playbook_bias = None
    try:
        from brain import master_strategist as _ms
        from brain import strategist_overlays as _ov
        _briefing = _ms.build_briefing()
        playbook_bias = _ov.compute_predictor_bias(_briefing)
        if playbook_bias and (playbook_bias.get("sector_bias")
                              or playbook_bias.get("symbol_bias")):
            print(f"[playbook-bias] fired cases: "
                  f"{playbook_bias.get('fired_case_ids', [])}")
            print(f"[playbook-bias] sector_bias: "
                  f"{playbook_bias.get('sector_bias')}")
            print(f"[playbook-bias] symbol_bias: "
                  f"{playbook_bias.get('symbol_bias')}")
        else:
            print("[playbook-bias] no fired cases — predictor runs unmodified")
    except Exception as _e:
        print(f"[playbook-bias] WARN: {type(_e).__name__}: {_e} — skipping bias")

    log = load_log()
    new_records = []
    for sym in tickers:
        start = time.time()
        rec = generate_one(sym, provider, playbook_bias=playbook_bias)
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

    try:
        from scripts._health import write_status
        write_status(
            workflow="predictions",
            ok=bool(new_records),
            note=(f"{len(new_records)} new predictions, "
                  f"{len(log['predictions'])} in log"),
            payload={
                "new":   int(len(new_records)),
                "total": int(len(log["predictions"])),
                "symbols": [r.get("symbol") for r in new_records],
            },
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
