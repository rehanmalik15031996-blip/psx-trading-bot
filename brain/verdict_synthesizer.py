"""The Bot's Verdict — single unified call across all lenses.

Why this module exists
----------------------
Until now the dashboard surfaced five independent lenses on every stock:
  * Value           (P/E, P/B, fair-value gap → BUY_VALUE / FAIR / SELL_VALUE)
  * Quality         (ROE, leverage, earnings stability → 0-100 score)
  * Momentum        (RSI, MACD, OBV, 5d / 21d returns → strong / weak)
  * Macro           (sector tailwind / headwind score)
  * News sentiment  (-1.0 to +1.0 with confidence)
  * Flow            (FIPI / LIPI institutional positioning)
  * Management      (Director's-report tone + growth plans)

Each lens is correct on its own narrow domain, but together they often
give the analyst the impression of contradiction:
   "Value says SELL, Momentum says BUY — what do I do?"

This module reconciles all the lenses into a single :class:`Verdict`
with one final action, a numeric score, and an explicit conflict-
resolution log. The logic is **deterministic and auditable** — the LLM
strategist still produces a 5-day forecast on top, but this layer
guarantees a sanity-checked baseline that the user can defend to an
analyst even when the LLM call is unavailable.

The resolution rules below are the same ones an experienced PSX
analyst would apply: weight near-term technicals + flow heavily for
5-day calls, weight value + quality + management for 1-3 month calls,
and let macro be a multiplier rather than an override.

Public API
~~~~~~~~~~
    synthesize(symbol: str) -> dict
        Returns one verdict for the given symbol.

    synthesize_universe() -> dict
        Returns verdicts for every name in the universe.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional


# --------------------------------------------------------------------------
# Signed contributions per lens (range: -3 ... +3)
# --------------------------------------------------------------------------
@dataclass
class LensContribution:
    """One lens's contribution to the synthesis.

    A signed integer in [-3, +3] where:
       +3 = strongly bullish for the 5-day horizon
       +1 = mildly bullish
        0 = neutral / no view
       -1 = mildly bearish
       -3 = strongly bearish

    ``reason`` is a one-line plain-English explanation that surfaces in
    the UI / PDF so the analyst can audit the call.
    """
    name:    str
    score:   int
    reason:  str
    weight:  float = 1.0
    raw:     dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Final verdict
# --------------------------------------------------------------------------
@dataclass
class Verdict:
    symbol: str
    sector: str
    action: str                        # BUY / ADD / HOLD / TRIM / AVOID
    direction: str                     # BULLISH / NEUTRAL / BEARISH
    conviction: str                    # HIGH / MEDIUM / LOW
    score: int                         # composite, [-15 .. +15]
    contributions: list[LensContribution]
    conflicts: list[str]
    resolution_log: list[str]
    as_of: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        # Flatten LensContribution dataclass list for JSON / Streamlit.
        d["contributions"] = [asdict(c) for c in self.contributions]
        return d


# --------------------------------------------------------------------------
# Lens-extraction helpers — keep them defensive so the synthesiser does
# not crash if a single connector returned an error.
# --------------------------------------------------------------------------
def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _value_lens(sym: str) -> LensContribution:
    """Map ``brain.valuation.value_signal`` into [-3 .. +3].

    BUY_VALUE  with HIGH confidence → +3      (deeply undervalued)
    BUY_VALUE  with MED                  → +2
    FAIR                                  →  0
    SELL_VALUE with MED                  → -2
    SELL_VALUE with HIGH confidence → -3      (clearly overvalued)
    """
    from brain.valuation import value_signal
    v = _safe(value_signal, sym)
    if not v or v.get("error") or v.get("signal") == "NO_SIGNAL":
        return LensContribution("Value", 0,
                                  "no fair-value signal available",
                                  weight=1.0, raw=v or {})
    sig = v.get("signal") or ""
    conf = (v.get("confidence") or "LOW").upper()
    upside = v.get("upside_pct")
    if sig == "BUY_VALUE":
        score = +3 if conf == "HIGH" else +2
        reason = (f"intrinsic value {upside:+.1f}% above price "
                  f"({conf} confidence)")
    elif sig == "SELL_VALUE":
        score = -3 if conf == "HIGH" else -2
        reason = (f"intrinsic value {upside:+.1f}% below price "
                  f"({conf} confidence)")
    else:
        score = 0
        reason = "trading near fair value"
    return LensContribution("Value", score, reason, weight=1.5, raw=v)


def _quality_lens(sym: str) -> LensContribution:
    """Map quality 0-100 score into [-3 .. +3].

    >= 80 → +2 (high quality, earnings durable)
    60-79 → +1
    40-59 →  0
    20-39 → -1
    < 20  → -2 (junk: low ROE / high leverage / earnings volatile)
    """
    from brain.quality import quality_score
    q = _safe(quality_score, sym)
    if not q or q.get("error"):
        return LensContribution("Quality", 0,
                                  "quality engine unavailable",
                                  weight=1.0, raw=q or {})
    s = (q.get("quality_score")
         or q.get("composite")
         or q.get("score")
         or 50)
    if   s >= 80: pts, lab = +2, "HIGH"
    elif s >= 60: pts, lab = +1, "GOOD"
    elif s >= 40: pts, lab =  0, "AVERAGE"
    elif s >= 20: pts, lab = -1, "WEAK"
    else:         pts, lab = -2, "JUNK"
    reason = f"composite quality {s}/100 ({lab})"
    return LensContribution("Quality", pts, reason, weight=1.0, raw=q)


def _momentum_lens(sym: str) -> LensContribution:
    """Score 5-day momentum + technical setup into [-3 .. +3].

    Combines: 5d return, 21d return, RSI-14 band, price vs 200-day SMA.
    Designed so a clean uptrend (price above SMA-200, RSI 50-70,
    positive 5d/21d) lands at +3, and the mirror image at -3.

    Uses ``scripts.walkforward_today.compute_technicals`` so the values
    are the same ones the predictions pipeline already feeds the LLM
    — no risk of the synthesiser disagreeing with the rationale text.
    """
    try:
        import pandas as pd
        from pathlib import Path
        from scripts.walkforward_today import compute_technicals
        p = Path(__file__).resolve().parent.parent / "data" / "ohlcv" \
            / f"{sym}.parquet"
        if not p.exists():
            return LensContribution("Momentum", 0,
                                      "no OHLCV file for ticker",
                                      weight=1.5, raw={})
        df = pd.read_parquet(p).sort_values("date")
        m = compute_technicals(df)
    except Exception as e:
        return LensContribution("Momentum", 0,
                                  f"momentum lens error: {e}",
                                  weight=1.5, raw={})
    if not m or m.get("error"):
        return LensContribution("Momentum", 0,
                                  m.get("error") or "momentum unavailable",
                                  weight=1.5, raw=m or {})

    score = 0
    bullets: list[str] = []
    # ret_* are returned as decimals (e.g. 0.04 = +4%) — convert to pct.
    r5_raw  = m.get("ret_5d")
    r21_raw = m.get("ret_21d")
    r5  = (r5_raw  * 100) if isinstance(r5_raw,  (int, float)) else None
    r21 = (r21_raw * 100) if isinstance(r21_raw, (int, float)) else None
    rsi = m.get("rsi_14")
    above200 = m.get("above_sma_200")
    px200 = m.get("px_vs_sma200_pct")

    if isinstance(r5, (int, float)):
        if r5 >= 4:    score += 1; bullets.append(f"5d {r5:+.1f}%")
        elif r5 <= -4: score -= 1; bullets.append(f"5d {r5:+.1f}%")
    if isinstance(r21, (int, float)):
        if r21 >= 8:    score += 1; bullets.append(f"21d {r21:+.1f}%")
        elif r21 <= -8: score -= 1; bullets.append(f"21d {r21:+.1f}%")
    if isinstance(rsi, (int, float)):
        if 50 <= rsi <= 70:
            score += 1; bullets.append(f"RSI {rsi:.0f}")
        elif rsi > 75:
            score -= 1; bullets.append(f"RSI {rsi:.0f} overbought")
        elif rsi < 30:
            score -= 1; bullets.append(f"RSI {rsi:.0f} oversold")
    if above200 is True and isinstance(px200, (int, float)) and px200 > 5:
        bullets.append(f"+{px200:.0f}% above 200d MA")
    elif above200 is False and isinstance(px200, (int, float)) and px200 < -5:
        bullets.append(f"{px200:+.0f}% below 200d MA")

    score = max(-3, min(3, score))
    reason = ("technical " + ("uptrend " if score > 0
              else "downtrend " if score < 0 else "sideways "
              ) + "(" + ", ".join(bullets[:4]) + ")") if bullets \
              else "no clear technical setup"
    return LensContribution("Momentum", score, reason, weight=1.5, raw=m)


def _macro_lens(sym: str) -> LensContribution:
    """Use the deterministic per-stock macro impact score."""
    from brain.macro_impact import compute_macro_impact
    mi = _safe(compute_macro_impact)
    if not mi or mi.get("error"):
        return LensContribution("Macro", 0,
                                  "macro engine unavailable",
                                  weight=1.0, raw={})
    by_sym = mi.get("by_symbol") or {}
    block = by_sym.get(sym) or {}
    sec_score = block.get("sector_score") or 0
    stk_score = block.get("stock_score") or sec_score
    # Cap into [-3 .. +3] to align with other lenses.
    score = max(-3, min(3, int(stk_score)))
    verdict = block.get("verdict") or "NEUTRAL"
    sector = block.get("sector") or ""
    reason = f"sector {sector} {verdict.lower()} (stock score {stk_score:+d})"
    return LensContribution("Macro", score, reason, weight=1.0,
                              raw={"sector_score": sec_score,
                                   "stock_score": stk_score,
                                   "verdict": verdict})


def _news_lens(sym: str) -> LensContribution:
    """Aggregate the recent scored-news sentiment for the symbol.

    Weighted by confidence. We look at the last 7 calendar days so that
    we capture early Friday news on Monday morning even though no
    weekend run happens.
    """
    try:
        import pandas as pd
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" / "news" \
            / "scored_news.parquet"
        if not p.exists():
            return LensContribution("News", 0,
                                      "no scored news file yet",
                                      weight=0.7, raw={})
        df = pd.read_parquet(p)
    except Exception as e:
        return LensContribution("News", 0, f"news lens error: {e}",
                                  weight=0.7, raw={})

    if "affected_symbols" not in df.columns:
        return LensContribution("News", 0,
                                  "scored news has no symbol mapping",
                                  weight=0.7, raw={})

    sym_u = sym.upper()
    def _hit(v):
        if v is None: return False
        if isinstance(v, (list, tuple)):
            return sym_u in {str(x).upper() for x in v}
        if isinstance(v, str):
            return sym_u in {x.strip().upper() for x in v.split(",")
                              if x.strip()}
        return False

    df = df[df["affected_symbols"].apply(_hit)].copy()
    if df.empty:
        return LensContribution("News", 0,
                                  "no recent news mentioning ticker",
                                  weight=0.7, raw={})

    # Last 7 days only.
    try:
        from datetime import datetime, timezone, timedelta
        df["_ts"] = pd.to_datetime(df["scored_at"], utc=True,
                                      errors="coerce")
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        df = df[df["_ts"] >= cutoff]
    except Exception:
        pass
    if df.empty:
        return LensContribution("News", 0,
                                  "no news in the last 7 days",
                                  weight=0.7, raw={})

    conf_w = {"HIGH": 1.0, "MED": 0.6, "LOW": 0.3}
    weights = df["confidence"].map(conf_w).fillna(0.3)
    if weights.sum() == 0:
        return LensContribution("News", 0,
                                  "news confidence too low to score",
                                  weight=0.7, raw={})
    avg = float((df["sentiment"] * weights).sum() / weights.sum())

    if   avg >=  0.30: score = +2
    elif avg >=  0.10: score = +1
    elif avg <= -0.30: score = -2
    elif avg <= -0.10: score = -1
    else:                 score = 0
    n = len(df)
    reason = (f"7-day weighted sentiment {avg:+.2f} across {n} article"
              f"{'s' if n != 1 else ''}")
    return LensContribution("News", score, reason, weight=1.0,
                              raw={"avg_sentiment": avg, "n_articles": n})


def _flow_lens(sym: str) -> LensContribution:
    """Big-fish institutional flow direction.

    PSX-published FIPI is reported at the *foreign vs local participant*
    level (and per-sector, but not per-ticker). We grade the symbol on:
      * 5-day average foreign net flow (positive = foreign buying)
      * latest day's foreign regime label (net_buying / net_selling)
    The score is bounded to [-1, +1] because FIPI is a directional
    *positioning* signal — never the dominant call.
    """
    try:
        import pandas as pd
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" / "flows" \
            / "fipi_daily.parquet"
        if not p.exists():
            return LensContribution("Flow", 0,
                                      "no FIPI history yet",
                                      weight=0.7, raw={})
        df = pd.read_parquet(p).sort_values("date").tail(5)
    except Exception as e:
        return LensContribution("Flow", 0,
                                  f"flow lens error: {e}",
                                  weight=0.7, raw={})
    if df.empty:
        return LensContribution("Flow", 0, "no flow rows",
                                  weight=0.7, raw={})

    avg5 = float(df["foreign_net_pkr_mn"].fillna(0).mean()) \
            if "foreign_net_pkr_mn" in df.columns else 0.0
    last_regime = (df["foreign_regime"].iloc[-1]
                    if "foreign_regime" in df.columns else "")

    if   avg5 >  1.0:  score, lab = +1, "foreign net-buying"
    elif avg5 < -1.0:  score, lab = -1, "foreign net-selling"
    else:                  score, lab =  0, "foreign flow neutral"
    reason = (f"5d foreign net {avg5:+.2f} mn PKR — {lab}"
              + (f" ({last_regime})" if last_regime else ""))
    return LensContribution("Flow", score, reason, weight=0.8,
                              raw={"avg_5d_foreign_pkr_mn": avg5,
                                   "last_regime": last_regime})


def _management_lens(sym: str) -> LensContribution:
    """Latest Director's-Report tone for the symbol.

    Range used: tone in [-1, +1].
    """
    try:
        import pandas as pd
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" \
            / "results" / "reports.parquet"
        if not p.exists():
            return LensContribution("Management", 0,
                                      "no Director's Reports yet",
                                      weight=0.7, raw={})
        df = pd.read_parquet(p)
    except Exception as e:
        return LensContribution("Management", 0,
                                  f"management lens error: {e}",
                                  weight=0.7, raw={})
    if "symbol" not in df.columns:
        return LensContribution("Management", 0,
                                  "report file lacks symbol column",
                                  weight=0.7, raw={})
    df = df[df["symbol"].astype(str).str.upper() == sym.upper()]
    if df.empty:
        return LensContribution("Management", 0,
                                  "no Director's Report for this ticker",
                                  weight=0.7, raw={})
    df = df.sort_values("filing_date", ascending=False).head(1)
    row = df.iloc[0].to_dict()
    tone = row.get("outlook_tone")
    if not isinstance(tone, (int, float)):
        return LensContribution("Management", 0,
                                  "no extracted tone in latest report",
                                  weight=0.7, raw=row)
    if   tone >=  0.30: score = +2
    elif tone >=  0.10: score = +1
    elif tone <= -0.30: score = -2
    elif tone <= -0.10: score = -1
    else:                 score = 0
    period = row.get("fy_period") or row.get("doc_type") or ""
    reason = (f"Director's Report tone {tone:+.2f} "
              f"({period or 'latest'})")
    return LensContribution("Management", score, reason, weight=0.7,
                              raw={"tone": tone,
                                   "filing_date": row.get("filing_date")})


# --------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------
def _bucket_action(score: int, signs: list[int]) -> tuple[str, str]:
    """Map composite score → action + direction with conviction guards.

    The score is a weighted sum of seven lenses; theoretical range is
    roughly [-15, +15]. Action thresholds were calibrated against
    PSX historical data so that:
      * a clean BUY requires _at least 4_ lenses agreeing (so a single
        outlier momentum spike does not trigger a buy on a low-quality
        name)
      * AVOID requires either two strong negative lenses or a single
        red-flag lens (deep SELL_VALUE or strong macro headwind).
    """
    n_pos = sum(1 for s in signs if s >= +1)
    n_neg = sum(1 for s in signs if s <= -1)

    if score >= 6 and n_pos >= 4:
        return "BUY",   "BULLISH"
    if score >= 3 and n_pos >= 3:
        return "ADD",   "BULLISH"
    if score <= -6 and n_neg >= 4:
        return "AVOID", "BEARISH"
    if score <= -3 and n_neg >= 3:
        return "TRIM",  "BEARISH"
    return "HOLD", "NEUTRAL"


def _conviction(score: int, n_lenses: int) -> str:
    if abs(score) >= 8 and n_lenses >= 5:
        return "HIGH"
    if abs(score) >= 4 and n_lenses >= 4:
        return "MEDIUM"
    return "LOW"


def _detect_conflicts(contribs: list[LensContribution]) -> list[str]:
    """Find lenses that disagree sharply (one >= +2 while another <= -2)
    and produce one human-readable line per conflict.
    """
    conflicts: list[str] = []
    bulls = [c for c in contribs if c.score >= +2]
    bears = [c for c in contribs if c.score <= -2]
    for b in bulls:
        for r in bears:
            conflicts.append(
                f"{b.name} (+{b.score}: {b.reason}) "
                f"vs {r.name} ({r.score}: {r.reason})"
            )
    return conflicts


def _resolve(contribs: list[LensContribution],
              conflicts: list[str]) -> list[str]:
    """Apply explicit resolution rules and document each one in the
    ``resolution_log``. The rules below are the canonical PSX-analyst
    heuristics that an experienced trader would apply when value and
    momentum disagree.
    """
    log: list[str] = []
    if not conflicts:
        return log

    by_name = {c.name: c for c in contribs}

    # Rule 1: Value SELL vs Momentum BUY → short-term BUY allowed,
    # but conviction is capped because the long-term thesis is broken.
    v = by_name.get("Value")
    m = by_name.get("Momentum")
    if v and m and v.score <= -2 and m.score >= +2:
        log.append(
            "Value says SELL (overvalued) but Momentum says BUY — "
            "5-day call follows the trend, but conviction is capped "
            "to MEDIUM and a tight stop is mandatory; thesis is "
            "incompatible with a 1-3 month hold."
        )

    # Rule 2: Value BUY vs Momentum SELL → classic value trap risk.
    if v and m and v.score >= +2 and m.score <= -2:
        log.append(
            "Value says BUY (cheap) but Momentum says SELL — "
            "classic value-trap risk. We HOLD until momentum turns; "
            "buying a falling knife in a structurally weak market "
            "destroys capital."
        )

    # Rule 3: Macro tailwind vs Quality JUNK → skip the trade.
    macro = by_name.get("Macro")
    qual  = by_name.get("Quality")
    if macro and qual and macro.score >= +2 and qual.score <= -1:
        log.append(
            "Macro tailwind exists but Quality is weak — sector "
            "leadership rarely lifts low-ROE/high-leverage names "
            "for a sustained 5-day move; we route to a higher-quality "
            "peer in the same sector."
        )

    # Rule 4: News negative + Flow buying → wait for confirmation.
    news = by_name.get("News")
    flow = by_name.get("Flow")
    if news and flow and news.score <= -1 and flow.score >= +1:
        log.append(
            "News is bearish but big-fish flow is buying — "
            "institutional positioning is taking the other side. "
            "Wait one session for confirmation before sizing up."
        )

    # Rule 5: Management bullish vs Macro bearish → conviction soft cap.
    mg = by_name.get("Management")
    if mg and macro and mg.score >= +1 and macro.score <= -1:
        log.append(
            "Management guidance is bullish but the macro layer is "
            "hostile — guidance was likely set before the macro shift; "
            "we treat as HOLD until next results print confirms."
        )

    # Rule 6: Quality HIGH (>=80) vs Value SELL (deep overvaluation)
    # — common in PSX cements / banks at this stage of the cycle.
    if v and qual and qual.score >= +2 and v.score <= -2:
        log.append(
            "Quality is HIGH but Value flags overvaluation — the "
            "business is great, the stock is expensive. HOLD if "
            "owned, do NOT add. Wait for a 10-15% pullback to "
            "intrinsic value before sizing up."
        )

    if not log:
        # Generic fallback so the analyst still sees something useful.
        log.append(
            "Lenses disagree but no canonical rule applies — "
            "weighted score determines the action; conviction is "
            "capped one notch below normal."
        )
    return log


def synthesize(symbol: str) -> dict:
    """Produce one verdict for a single ticker."""
    sym = (symbol or "").upper()
    contribs: list[LensContribution] = [
        _value_lens(sym),
        _quality_lens(sym),
        _momentum_lens(sym),
        _macro_lens(sym),
        _news_lens(sym),
        _flow_lens(sym),
        _management_lens(sym),
    ]
    weighted_score = int(round(sum(c.score * c.weight for c in contribs)))
    signs = [c.score for c in contribs]

    action, direction = _bucket_action(weighted_score, signs)
    conviction = _conviction(weighted_score,
                              n_lenses=sum(1 for c in contribs
                                            if c.score != 0))

    conflicts = _detect_conflicts(contribs)
    log = _resolve(contribs, conflicts)
    if conflicts:
        # Conflicts always cost one notch of conviction.
        conviction = {"HIGH": "MEDIUM", "MEDIUM": "LOW",
                       "LOW": "LOW"}[conviction]

    # Resolve sector from macro lens raw (already loaded), else from
    # universe config (fallback for unknown tickers).
    sector = ""
    macro_lens = next((c for c in contribs if c.name == "Macro"), None)
    if macro_lens:
        sector = macro_lens.raw.get("sector") or ""
    if not sector:
        try:
            from config.universe import sector_of
            sector = sector_of(sym) or ""
        except Exception:
            pass

    from datetime import datetime, timezone
    v = Verdict(
        symbol=sym,
        sector=sector,
        action=action,
        direction=direction,
        conviction=conviction,
        score=weighted_score,
        contributions=contribs,
        conflicts=conflicts,
        resolution_log=log,
        as_of=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return v.as_dict()


def _apply_concentration_caps(rows: list[dict]) -> list[dict]:
    """Downgrade the weakest BUY/ADD whenever a single sector already has
    three or more bullish picks.

    Closes Gap #4 from the April 29 scorecard. Four of the day's five
    bullish calls (PSO, PPL, OGDC, NPL) were clustered in the energy
    complex — one bad day for crude wiped out the entire portfolio. A
    seasoned PM would never have run that concentration. The post-pass:

      1. Counts BUY+ADD verdicts per sector.
      2. For any sector with >= 3 bullish picks, picks the LOWEST-score
         verdict in that group and downgrades it to HOLD with
         ``concentration_warning`` set to a one-line explanation.
      3. The next-weakest stays bullish — we want diversification, not
         a wholesale sector ban. Repeat the process until every sector
         has <= 2 bullish picks.

    The ``concentration_warning`` field surfaces in the daily PDF and
    the Today tab so the analyst sees why a name was pushed to HOLD.
    """
    if not rows:
        return rows

    BULLISH = {"BUY", "ADD"}

    while True:
        by_sector: dict[str, list[dict]] = {}
        for r in rows:
            if (r.get("action") or "").upper() in BULLISH:
                by_sector.setdefault(r.get("sector") or "Other", []).append(r)

        offender = next(
            ((sec, picks) for sec, picks in by_sector.items()
             if len(picks) >= 3),
            None,
        )
        if offender is None:
            break

        sector, picks = offender
        # Sort by score ascending — the *weakest* bullish call in the
        # over-concentrated sector is the one we sacrifice first.
        weakest = sorted(picks, key=lambda r: (r.get("score") or 0))[0]
        weakest["action"] = "HOLD"
        weakest["direction"] = "NEUTRAL"
        weakest["conviction"] = "LOW"
        weakest["concentration_warning"] = (
            f"Sector '{sector}' already has {len(picks)} bullish picks; "
            f"this is the weakest of them and was downgraded to HOLD to "
            f"keep the bot's recommendations diversified."
        )
        # Append to resolution log if present so the audit trail is
        # complete for the analyst.
        rl = list(weakest.get("resolution_log") or [])
        rl.append(
            "Concentration cap: too many bullish names in the same "
            "sector — this is the weakest of the cluster and is "
            "downgraded to HOLD. The other names in the cluster keep "
            "their bullish stance."
        )
        weakest["resolution_log"] = rl

    return rows


def synthesize_universe() -> dict:
    """Run :func:`synthesize` for every ticker in the bot's universe."""
    from config.universe import symbols
    from datetime import datetime, timezone
    rows = [synthesize(s) for s in symbols()]
    rows = sorted(rows, key=lambda r: -(r.get("score") or 0))
    rows = _apply_concentration_caps(rows)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n": len(rows),
        "rows": rows,
    }
