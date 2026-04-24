"""Intraday walk-forward: predict OPEN, CLOSE, range, and session bias
from yesterday's data, then score against the actual Apr-23 bar.

Data cutoff: 2026-04-22 close.
Target bar : 2026-04-23 (we have the actual open + close in OHLCV parquets).

For each of the 6 required tickers we:
  1. Slice OHLCV + macro to the cutoff and compute:
     a. Standard technicals (SMA, momentum, RSI, vol)
     b. 90-day intraday stats: avg overnight gap, avg intraday drift,
        |daily move| mean + stddev  ->  *this gives the LLM real anchor
        numbers instead of pulling ranges out of thin air*.
  2. Fetch RSS articles published on or before the cutoff.
  3. Send Claude a briefing and ask it to return a structured intraday plan:
     predicted open / close / intraday range / morning vs afternoon bias /
     specific buy & sell zones / key support & resistance.
  4. Score against the actual open + close: gap hit, close direction hit,
     was actual close inside the predicted band?, how far off was the point
     estimate?

NOTE: PSX OHLCV history we have stored is (date, symbol, open, close,
volume). No intraday high/low. We therefore *estimate* the daily range from
|close - open| patterns + annualized 20d volatility; predictions of
intraday high/low cannot be empirically scored in this run.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _load_dotenv(p: Path) -> None:
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ROOT / ".env")

import numpy as np
import pandas as pd

from ui.overnight import build_overnight_block, gap_bias_from_overnight, load_overnight


# --------------------------------------------------------------------------
TICKERS = ["HUBC", "PABC", "MLCF", "OGDC", "FABL", "PPL"]
CUTOFF = pd.Timestamp("2026-04-22")
TARGET = pd.Timestamp("2026-04-23")


# --------------------------------------------------------------------------
# Historical intraday stats (90 trading days, ending at cutoff)
# --------------------------------------------------------------------------
def intraday_stats(df: pd.DataFrame, lookback: int = 90) -> dict:
    w = df.tail(lookback).copy()
    if len(w) < 20:
        return {}
    w["prev_close"] = w["close"].shift(1)
    w["gap_pct"] = (w["open"] / w["prev_close"] - 1) * 100
    w["intra_pct"] = (w["close"] / w["open"] - 1) * 100
    w["day_pct"] = (w["close"] / w["prev_close"] - 1) * 100
    return {
        "lookback_days": len(w),
        "avg_gap_pct": round(float(w["gap_pct"].mean()), 2),
        "avg_abs_gap_pct": round(float(w["gap_pct"].abs().mean()), 2),
        "sd_gap_pct": round(float(w["gap_pct"].std()), 2),
        "avg_intraday_pct": round(float(w["intra_pct"].mean()), 2),
        "avg_abs_intraday_pct": round(float(w["intra_pct"].abs().mean()), 2),
        "sd_intraday_pct": round(float(w["intra_pct"].std()), 2),
        "avg_abs_day_pct": round(float(w["day_pct"].abs().mean()), 2),
        "sd_day_pct": round(float(w["day_pct"].std()), 2),
        # session tendency: fraction of days that gap up (green open)
        "pct_days_gap_up": round(float((w["gap_pct"] > 0).mean() * 100), 1),
        # fraction of days that close red from open (intraday fade)
        "pct_days_intraday_red": round(float((w["intra_pct"] < 0).mean() * 100), 1),
    }


def compute_technicals(df: pd.DataFrame) -> dict:
    close = df["close"].astype(float).values
    last = float(close[-1])
    if len(close) < 60:
        return {"error": "need >=60 bars"}

    def _log_ret(n):
        return (float(np.log(close[-1] / close[-1 - n]))
                if len(close) > n else None)

    def _ret(n):
        return (round(float(close[-1] / close[-1 - n] - 1), 4)
                if len(close) > n else None)

    def _sma(n):
        return float(np.mean(close[-n:])) if len(close) >= n else None

    def _rsi(n=14):
        if len(close) <= n:
            return None
        d = np.diff(close[-(n + 1):])
        g = np.where(d > 0, d, 0).sum() / n
        l = -np.where(d < 0, d, 0).sum() / n
        if l == 0:
            return 100.0
        return round(float(100 - 100 / (1 + g / l)), 1)

    logret = np.diff(np.log(close))
    rvol20 = (float(np.std(logret[-20:]) * np.sqrt(252))
              if len(logret) >= 20 else None)
    sma200 = _sma(200)

    return {
        "close_pkr": round(last, 2),
        "ret_1d": _ret(1),
        "ret_5d": _ret(5),
        "ret_21d": _ret(21),
        "ret_63d": _ret(63),
        "mom_20d_log": round(_log_ret(20), 4) if _log_ret(20) is not None else None,
        "mom_60d_log": round(_log_ret(60), 4) if _log_ret(60) is not None else None,
        "mom_150d_log": round(_log_ret(150), 4)
                         if _log_ret(150) is not None else None,
        "sma_20": round(_sma(20), 2) if _sma(20) else None,
        "sma_50": round(_sma(50), 2) if _sma(50) else None,
        "sma_200": round(sma200, 2) if sma200 else None,
        "px_vs_sma200_pct": round((last / sma200 - 1) * 100, 2) if sma200 else None,
        "rvol_20d_ann": round(rvol20, 4) if rvol20 else None,
        "rsi_14": _rsi(14),
        "high_52w": round(float(np.max(close[-252:])), 2) if len(close) >= 60 else None,
        "low_52w": round(float(np.min(close[-252:])), 2) if len(close) >= 60 else None,
    }


def macro_at_cutoff(cutoff: pd.Timestamp) -> dict:
    out = {}
    for key, label in [("usdpkr", "USD/PKR"), ("brent", "Brent USD/bbl"),
                       ("wti", "WTI USD/bbl"), ("gold", "Gold USD/oz")]:
        path = ROOT / "data" / "macro" / f"{key}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path).sort_values("date")
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] <= cutoff]
        col = "close" if "close" in df.columns else "value"
        if df.empty or col not in df.columns:
            continue
        last_val = float(df[col].iloc[-1])

        def _r(n, s=df[col]):
            return (round(last_val / float(s.iloc[-n - 1]) - 1, 4)
                    if len(s) > n and s.iloc[-n - 1] else None)

        out[key] = {
            "label": label, "value": round(last_val, 2),
            "ret_5d": _r(5), "ret_21d": _r(21), "ret_63d": _r(63),
        }
    return out


# --------------------------------------------------------------------------
# News filtering (strictly before TARGET)
# --------------------------------------------------------------------------
NEWS_KEYWORDS = {
    "HUBC": ["HUBCO", "Hub Power", "Hubco", "power", "IPP"],
    "PABC": ["Pak Arab", "PABC", "refinery", "aluminum", "cans"],
    "MLCF": ["Maple Leaf", "MLCF", "cement"],
    "OGDC": ["OGDC", "oil and gas development", "Oil & Gas Dev", "gas", "crude"],
    "FABL": ["Faysal", "FABL", "banking"],
    "PPL": ["Pakistan Petroleum", "PPL", "gas", "crude", "brent"],
}


def news_at_cutoff(cutoff: pd.Timestamp) -> list[dict]:
    from connectors.rss_news import RssNewsConnector
    r = RssNewsConnector().fetch(per_feed=10)
    if not r.ok:
        return []
    kept = []
    for a in r.records:
        ts = a.get("published_at")
        if not ts:
            continue
        try:
            pub = pd.to_datetime(ts)
        except (ValueError, TypeError):
            continue
        if pub.normalize() <= cutoff:
            kept.append(a)
    return kept


def match_news(articles, symbol, limit=6):
    kw = [w.lower() for w in NEWS_KEYWORDS.get(symbol, [symbol])]
    hits = [a for a in articles
            if any(w in ((a.get("title") or "") + " "
                         + (a.get("summary") or "")).lower() for w in kw)]
    return (hits or articles)[:limit]


# --------------------------------------------------------------------------
# Claude intraday prediction
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a PSX intraday trading analyst. The Pakistan
Stock Exchange opens 09:32 PKT and closes 15:30 PKT. Your job is to predict
the NEXT TRADING DAY's session for a single stock, using only the briefing
below. NEVER invent a headline or number that is not in the briefing.

Output ONLY one valid JSON object, no fences, no prose:
{
  "predicted_open_pkr": <float>,
  "predicted_open_gap_pct": <float vs yesterday close>,
  "predicted_close_pkr": <float>,
  "predicted_close_return_pct": <float vs yesterday close>,
  "predicted_intraday_high_pkr": <float>,
  "predicted_intraday_low_pkr": <float>,
  "morning_session_bias": "UP" | "DOWN" | "FLAT",
  "afternoon_session_bias": "UP" | "DOWN" | "FLAT",
  "intraday_narrative": "<2-4 sentences describing the expected session arc>",
  "buy_zone_pkr": [<low>, <high>],
  "sell_zone_pkr": [<low>, <high>],
  "stop_loss_pkr": <float>,
  "key_support_pkr": <float>,
  "key_resistance_pkr": <float>,
  "conviction": "LOW" | "MEDIUM" | "HIGH",
  "key_drivers": ["<fact>", "<fact>"],
  "key_risks": ["<fact>", "<fact>"]
}

CALIBRATION RULES (read before answering):

1. OVERNIGHT GAP DIRECTION. The briefing contains an "OVERNIGHT GLOBAL
   RISK" block with a RULES-BASED GAP PRIOR (GAP_UP / FLAT / GAP_DOWN).
   ANCHOR your predicted_open_gap_pct to that prior:
     - If prior says GAP_DOWN, predicted_open_gap_pct must be NEGATIVE
       unless a stock-specific bullish catalyst in today's news is strong
       enough to offset it.
     - If prior says GAP_UP, predicted_open_gap_pct is positive.
     - If prior says FLAT, gap is within +/- avg_abs_gap_pct from the
       historical stats.
   The historical avg_gap_pct is a WEAK prior; overnight risk dominates.

2. INTRADAY RANGE — VIX-CONDITIONAL (this was consistently too narrow).
   Base width = K * sd_day_pct * y_close / 100, where K is set by the VIX
   regime in the OVERNIGHT block:
     - VIX complacent (<14):  K = 1.3
     - VIX normal    (14-18): K = 1.5
     - VIX elevated  (18-22): K = 1.8
     - VIX stressed  (22-28): K = 2.2
     - VIX panic     (>=28):  K = 2.6
   Example: sd_day_pct = 3.0%, y_close = 100, VIX elevated -> range width
   = 1.8 * 3.0% * 100 = 5.4 PKR. Bias the range asymmetrically toward the
   gap direction: if prior is GAP_DOWN, low bound sits ~0.6*width below
   predicted_close and high bound ~0.4*width above (and vice versa).
   Both predicted_open and predicted_close MUST sit inside the range.

3. CLOSE vs OPEN. Given gap direction, intraday drift on PSX tends to
   FADE the open (see pct_days_fade_intraday in the stats). So if the
   open is a GAP_UP, the close often comes back down; if GAP_DOWN, the
   close often recovers partway. Factor this into predicted_close.

4. CONVICTION.
     HIGH = momentum + overnight prior + news + macro all aligned.
     MEDIUM = 2-3 of those aligned.
     LOW = conflicting signals or tight range.

5. buy_zone near predicted low and a real support level (SMA20 / recent
   swing low). sell_zone near predicted high and resistance.
   stop_loss ~ predicted_low - 0.5 * sd_day, or recent swing low, whichever
   is tighter.

6. If the overnight prior conflicts with stock momentum, explain the
   conflict in intraday_narrative and choose conviction = LOW."""


def predict_claude(briefing: str, sym: str, close: float) -> tuple[dict, str]:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=900,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Predict tomorrow's intraday session for {sym}. Yesterday's "
                f"close: {close} PKR. DATA BRIEFING follows.\n\n{briefing}\n\n"
                "Return the JSON plan now."
            ),
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text").strip()
    return parse_json(text), text


def parse_json(s: str) -> dict:
    t = s.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        return json.loads(t[a:b + 1])
    raise ValueError(f"could not parse JSON: {s[:200]}")


def build_briefing(sym: str, tech: dict, stats: dict, macro: dict,
                   articles: list[dict], overnight_block: str) -> str:
    def pct(v):
        return "N/A" if v is None else f"{v*100:+.2f}%"
    lines = [
        f"=== {sym} — cutoff {CUTOFF.date()}, predicting {TARGET.date()} ===",
        "",
        overnight_block,
        "",
        "TECHNICALS (at cutoff)",
        f"  Y-close: {tech.get('close_pkr')} PKR",
        f"  Returns: 1d={pct(tech.get('ret_1d'))}  5d={pct(tech.get('ret_5d'))}  "
        f"21d={pct(tech.get('ret_21d'))}  63d={pct(tech.get('ret_63d'))}",
        f"  Momentum (log): 20d={pct(tech.get('mom_20d_log'))}  "
        f"60d={pct(tech.get('mom_60d_log'))}  "
        f"150d={pct(tech.get('mom_150d_log'))}",
        f"  SMA20={tech.get('sma_20')}  SMA50={tech.get('sma_50')}  "
        f"SMA200={tech.get('sma_200')}  px_vs_sma200={tech.get('px_vs_sma200_pct')}%",
        f"  RSI14={tech.get('rsi_14')}  vol20_ann={tech.get('rvol_20d_ann')}  "
        f"52w_range=[{tech.get('low_52w')}, {tech.get('high_52w')}]",
        "",
        f"90-DAY INTRADAY STATISTICS (key anchors for your range prediction)",
        f"  avg_gap_pct       = {stats.get('avg_gap_pct')}%  "
        f"(|mean|={stats.get('avg_abs_gap_pct')}%  sd={stats.get('sd_gap_pct')}%)",
        f"  avg_intraday_pct  = {stats.get('avg_intraday_pct')}%  "
        f"(|mean|={stats.get('avg_abs_intraday_pct')}%  sd={stats.get('sd_intraday_pct')}%)",
        f"  avg_abs_day_pct   = {stats.get('avg_abs_day_pct')}%  "
        f"sd_day={stats.get('sd_day_pct')}%",
        f"  pct_days_gap_up   = {stats.get('pct_days_gap_up')}%",
        f"  pct_days_fade_intraday = {stats.get('pct_days_intraday_red')}%",
        "",
        "MACRO AT CUTOFF",
    ]
    for k, v in macro.items():
        lines.append(
            f"  {v.get('label')}: {v.get('value')}  "
            f"5d={pct(v.get('ret_5d'))}  21d={pct(v.get('ret_21d'))}  "
            f"63d={pct(v.get('ret_63d'))}"
        )
    lines += ["", f"NEWS (on or before {CUTOFF.date()})"]
    for a in articles:
        lines.append(
            f"  [{(a.get('published_at') or '?')[:10]}] "
            f"({a.get('source', '?')[:18]}) {a.get('title', '')[:140]}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
@dataclass
class Row:
    symbol: str
    y_close: float
    pred_open: float
    pred_open_gap_pct: float
    pred_close: float
    pred_close_return_pct: float
    pred_high: float
    pred_low: float
    morning_bias: str
    afternoon_bias: str
    buy_zone: list
    sell_zone: list
    stop: float
    support: float
    resistance: float
    conviction: str
    narrative: str
    drivers: list
    risks: list
    # actuals
    actual_open: float
    actual_close: float

    @property
    def actual_open_gap_pct(self) -> float:
        return round((self.actual_open / self.y_close - 1) * 100, 2)

    @property
    def actual_close_return_pct(self) -> float:
        return round((self.actual_close / self.y_close - 1) * 100, 2)

    @property
    def actual_intraday_pct(self) -> float:
        return round((self.actual_close / self.actual_open - 1) * 100, 2)

    @property
    def open_hit(self) -> bool:
        """Correct direction of the overnight gap."""
        p, a = self.pred_open_gap_pct, self.actual_open_gap_pct
        if abs(p) < 0.1 and abs(a) < 0.1:
            return True  # both flat
        return p * a > 0

    @property
    def close_hit(self) -> bool:
        p, a = self.pred_close_return_pct, self.actual_close_return_pct
        if abs(p) < 0.2 and abs(a) < 0.2:
            return True
        return p * a > 0

    @property
    def morning_hit(self) -> bool:
        """Did the predicted morning bias align with open-minus-yesterday sign?"""
        a = self.actual_open_gap_pct
        if self.morning_bias == "UP":
            return a > 0
        if self.morning_bias == "DOWN":
            return a < 0
        return abs(a) <= 0.5

    @property
    def afternoon_hit(self) -> bool:
        """Did the predicted afternoon bias align with close-minus-open sign?"""
        a = self.actual_intraday_pct
        if self.afternoon_bias == "UP":
            return a > 0
        if self.afternoon_bias == "DOWN":
            return a < 0
        return abs(a) <= 0.5

    @property
    def close_in_range(self) -> bool:
        return self.pred_low <= self.actual_close <= self.pred_high

    @property
    def open_in_range(self) -> bool:
        return self.pred_low <= self.actual_open <= self.pred_high

    @property
    def open_err_pct(self) -> float:
        return round((self.pred_open - self.actual_open) / self.actual_open * 100, 2)

    @property
    def close_err_pct(self) -> float:
        return round((self.pred_close - self.actual_close) / self.actual_close * 100, 2)


def main():
    print("\nIntraday walk-forward  (Apr 22 close -> Apr 23 session)")
    print("-" * 78)

    macro = macro_at_cutoff(CUTOFF)
    print(f"Macro: Brent={macro.get('brent', {}).get('value')}  "
          f"USD/PKR={macro.get('usdpkr', {}).get('value')}  "
          f"Gold={macro.get('gold', {}).get('value')}")
    overnight = load_overnight(CUTOFF)
    gap_prior = gap_bias_from_overnight(overnight)
    overnight_block = build_overnight_block(CUTOFF)
    print(f"Overnight @ {overnight.get('as_of', '?')}: "
          f"S&P 1d={overnight.get('sp500', {}).get('ret_1d_pct')}%  "
          f"VIX={overnight.get('vix', {}).get('close')}  "
          f"=> gap prior {gap_prior['bias']} ({gap_prior['expected_gap_pct']:+.2f}%)")
    print("Fetching + filtering news...")
    articles = news_at_cutoff(CUTOFF)
    print(f"  kept {len(articles)} articles on or before {CUTOFF.date()}")

    rows: list[Row] = []
    for sym in TICKERS:
        path = ROOT / "data" / "ohlcv" / f"{sym}.parquet"
        df = pd.read_parquet(path).sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"])
        hist = df[df["date"] <= CUTOFF]
        tgt = df[df["date"] == TARGET]
        if hist.empty or tgt.empty:
            print(f"[{sym}] missing, skip"); continue

        tech = compute_technicals(hist)
        stats = intraday_stats(hist, 90)
        y_close = float(hist["close"].iloc[-1])
        actual_open = float(tgt["open"].iloc[0])
        actual_close = float(tgt["close"].iloc[0])
        sym_news = match_news(articles, sym, 5)

        brief = build_briefing(sym, tech, stats, macro, sym_news, overnight_block)

        print(f"\n[{sym}] y_close={y_close}  actual open={actual_open}  "
              f"actual close={actual_close}  -> calling Claude")
        try:
            pred, _ = predict_claude(brief, sym, y_close)
        except Exception as e:
            print(f"  LLM fail: {type(e).__name__}: {e}"); continue

        row = Row(
            symbol=sym, y_close=round(y_close, 2),
            pred_open=round(float(pred["predicted_open_pkr"]), 2),
            pred_open_gap_pct=float(pred.get("predicted_open_gap_pct", 0)),
            pred_close=round(float(pred["predicted_close_pkr"]), 2),
            pred_close_return_pct=float(pred.get("predicted_close_return_pct", 0)),
            pred_high=round(float(pred["predicted_intraday_high_pkr"]), 2),
            pred_low=round(float(pred["predicted_intraday_low_pkr"]), 2),
            morning_bias=pred.get("morning_session_bias", "FLAT"),
            afternoon_bias=pred.get("afternoon_session_bias", "FLAT"),
            buy_zone=list(pred.get("buy_zone_pkr", [])),
            sell_zone=list(pred.get("sell_zone_pkr", [])),
            stop=round(float(pred.get("stop_loss_pkr", 0)), 2),
            support=round(float(pred.get("key_support_pkr", 0)), 2),
            resistance=round(float(pred.get("key_resistance_pkr", 0)), 2),
            conviction=pred.get("conviction", "LOW"),
            narrative=pred.get("intraday_narrative", ""),
            drivers=pred.get("key_drivers", []),
            risks=pred.get("key_risks", []),
            actual_open=round(actual_open, 2),
            actual_close=round(actual_close, 2),
        )
        rows.append(row)
        print(f"  pred open={row.pred_open} ({row.pred_open_gap_pct:+.2f}%)  "
              f"close={row.pred_close} ({row.pred_close_return_pct:+.2f}%)  "
              f"range=[{row.pred_low}, {row.pred_high}]  "
              f"{row.morning_bias}/{row.afternoon_bias}")

    # ----------------------------------------------------------------------
    # Scorecard
    # ----------------------------------------------------------------------
    print("\n" + "=" * 112)
    print("INTRADAY SCORECARD  (Apr 22 close -> Apr 23 open + close)")
    print("=" * 112)
    hdr = (f"{'SYM':<5s} {'Y-CLS':>7s}  "
           f"{'P-OPEN':>8s} {'A-OPEN':>8s} {'dGAP%':>6s} {'oHIT':>5s}  "
           f"{'P-CLS':>8s} {'A-CLS':>8s} {'dCLS%':>6s} {'cHIT':>5s}  "
           f"{'P-LO':>7s} {'P-HI':>7s} {'inRNG':>6s}  "
           f"{'M':>3s} {'A':>3s}")
    print(hdr)
    print("-" * 112)
    open_hits = close_hits = morn_hits = aft_hits = in_range = 0
    open_errs, close_errs = [], []
    for r in rows:
        print(f"{r.symbol:<5s} {r.y_close:>7.2f}  "
              f"{r.pred_open:>8.2f} {r.actual_open:>8.2f} "
              f"{r.pred_open_gap_pct - r.actual_open_gap_pct:>+6.2f} "
              f"{'HIT' if r.open_hit else 'MISS':>5s}  "
              f"{r.pred_close:>8.2f} {r.actual_close:>8.2f} "
              f"{r.pred_close_return_pct - r.actual_close_return_pct:>+6.2f} "
              f"{'HIT' if r.close_hit else 'MISS':>5s}  "
              f"{r.pred_low:>7.2f} {r.pred_high:>7.2f} "
              f"{'IN' if r.close_in_range else 'OUT':>6s}  "
              f"{('HIT' if r.morning_hit else 'MISS')[:3]:>3s} "
              f"{('HIT' if r.afternoon_hit else 'MISS')[:3]:>3s}")
        open_hits += r.open_hit
        close_hits += r.close_hit
        morn_hits += r.morning_hit
        aft_hits += r.afternoon_hit
        in_range += r.close_in_range
        open_errs.append(abs(r.open_err_pct))
        close_errs.append(abs(r.close_err_pct))

    n = len(rows)
    if n:
        print("-" * 112)
        print(f"Open direction hit rate     : {open_hits}/{n} = {open_hits/n*100:.0f}%  "
              f"(mean abs open error: {sum(open_errs)/n:.2f}%)")
        print(f"Close direction hit rate    : {close_hits}/{n} = {close_hits/n*100:.0f}%  "
              f"(mean abs close error: {sum(close_errs)/n:.2f}%)")
        print(f"Close inside predicted range: {in_range}/{n} = {in_range/n*100:.0f}%")
        print(f"Morning bias hit rate       : {morn_hits}/{n} = {morn_hits/n*100:.0f}%")
        print(f"Afternoon bias hit rate     : {aft_hits}/{n} = {aft_hits/n*100:.0f}%")

    # Per-ticker plans (what a trader would actually act on)
    print("\n" + "=" * 112)
    print("TRADE PLANS (predicted ahead of Apr 23 open)")
    print("=" * 112)
    for r in rows:
        print(f"\n[{r.symbol}]  conviction={r.conviction}   "
              f"morning={r.morning_bias}  afternoon={r.afternoon_bias}")
        print(f"  Path: open~{r.pred_open} -> high~{r.pred_high} -> low~{r.pred_low} "
              f"-> close~{r.pred_close}")
        print(f"  Buy zone : {r.buy_zone}   Sell zone: {r.sell_zone}")
        print(f"  Stop: {r.stop}   Support: {r.support}   Resistance: {r.resistance}")
        print(f"  Narrative: {r.narrative}")
        print(f"  Actual:   open={r.actual_open} ({r.actual_open_gap_pct:+.2f}%)  "
              f"close={r.actual_close} ({r.actual_close_return_pct:+.2f}%)  "
              f"intraday={r.actual_intraday_pct:+.2f}%")

    out = ROOT / "reports" / "walkforward_intraday_v2.json"
    out.parent.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cutoff": str(CUTOFF.date()), "target": str(TARGET.date()),
        "predictions": [asdict(r) for r in rows],
        "summary": {
            "open_hits": open_hits, "close_hits": close_hits,
            "morning_hits": morn_hits, "afternoon_hits": aft_hits,
            "close_in_range": in_range, "n": n,
            "mean_abs_open_err_pct": round(sum(open_errs) / n, 2) if n else None,
            "mean_abs_close_err_pct": round(sum(close_errs) / n, 2) if n else None,
        },
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
