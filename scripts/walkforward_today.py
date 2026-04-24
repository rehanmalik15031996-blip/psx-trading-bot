"""Walk-forward nowcast: predict TODAY from YESTERDAY's data, then score.

Data cutoff: end of 2026-04-22 (inclusive).
Prediction target: next trading day (2026-04-23).
Horizon: 1 trading day.

For each of the 6 required tickers we:
  1. Slice OHLCV, macro parquets, and RSS news to the cutoff.
  2. Rebuild technicals (momentum 20/60/150/250, SMA20/50/200, RSI14, vol20,
     52w range) on the sliced history.
  3. Send Claude a briefing with explicit instructions to predict ONLY the
     next bar's close in PKR plus a 1-day return band.
  4. Pull the actual Apr-23 close from the OHLCV parquet.
  5. Print a side-by-side scorecard.

NOTE: FIPI flows are live-only (no historical cache), so we omit them from
this walk-forward. Live RSS pulls today will include articles published
this morning — we filter them out by `published_at < cutoff+1 day`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
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


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
TICKERS = ["HUBC", "PABC", "MLCF", "OGDC", "FABL", "PPL"]
CUTOFF = pd.Timestamp("2026-04-22")         # last bar the "trader" sees
TARGET = pd.Timestamp("2026-04-23")         # predicted bar


# --------------------------------------------------------------------------
# Technicals on a historical slice (no look-ahead)
# --------------------------------------------------------------------------
def compute_technicals(df: pd.DataFrame) -> dict:
    """df must be sorted ascending by date and end at the cutoff."""
    if len(df) < 60:
        return {"error": f"need >=60 bars, have {len(df)}"}
    close = df["close"].astype(float).values
    last = float(close[-1])

    def _log_ret(n):
        if len(close) <= n:
            return None
        return float(np.log(close[-1] / close[-1 - n]))

    def _sma(n):
        if len(close) < n:
            return None
        return float(np.mean(close[-n:]))

    def _rsi(n=14):
        if len(close) <= n:
            return None
        diff = np.diff(close[-(n + 1):])
        gains = np.where(diff > 0, diff, 0.0).sum() / n
        losses = -np.where(diff < 0, diff, 0.0).sum() / n
        if losses == 0:
            return 100.0
        rs = gains / losses
        return round(float(100 - 100 / (1 + rs)), 1)

    def _ret(n):
        if len(close) <= n:
            return None
        return round(float(close[-1] / close[-1 - n] - 1), 4)

    logret = np.diff(np.log(close))
    rvol20 = (float(np.std(logret[-20:]) * np.sqrt(252))
              if len(logret) >= 20 else None)

    sma20 = _sma(20); sma50 = _sma(50); sma200 = _sma(200)
    high_52w = float(np.max(close[-252:])) if len(close) >= 60 else None
    low_52w = float(np.min(close[-252:])) if len(close) >= 60 else None

    return {
        "close_pkr": round(last, 2),
        "ret_1d": _ret(1),
        "ret_5d": _ret(5),
        "ret_21d": _ret(21),
        "ret_63d": _ret(63),
        "ret_252d": _ret(252),
        "mom_20d_log": round(_log_ret(20), 4) if _log_ret(20) is not None else None,
        "mom_60d_log": round(_log_ret(60), 4) if _log_ret(60) is not None else None,
        "mom_150d_log": round(_log_ret(150), 4) if _log_ret(150) is not None else None,
        "mom_250d_log": round(_log_ret(250), 4) if _log_ret(250) is not None else None,
        "sma_20": round(sma20, 2) if sma20 else None,
        "sma_50": round(sma50, 2) if sma50 else None,
        "sma_200": round(sma200, 2) if sma200 else None,
        "px_vs_sma200_pct": round((last / sma200 - 1) * 100, 2) if sma200 else None,
        "above_sma_200": bool(sma200 and last > sma200),
        "rvol_20d_ann": round(rvol20, 4) if rvol20 else None,
        "rsi_14": _rsi(14),
        "high_52w": round(high_52w, 2) if high_52w else None,
        "low_52w": round(low_52w, 2) if low_52w else None,
        "dist_from_52w_high_pct": round((last / high_52w - 1) * 100, 2)
                                   if high_52w else None,
    }


# --------------------------------------------------------------------------
# Macro snapshot at cutoff
# --------------------------------------------------------------------------
def macro_at_cutoff(cutoff: pd.Timestamp) -> dict:
    indicators = {}
    keys = {"usdpkr": "USD/PKR", "brent": "Brent (USD/bbl)",
            "wti": "WTI (USD/bbl)", "gold": "Gold (USD/oz)"}
    for key, label in keys.items():
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

        def _ret(n, s=df[col]):
            if len(s) > n:
                prev = float(s.iloc[-n - 1])
                return round(last_val / prev - 1, 4) if prev else None
            return None

        indicators[key] = {
            "label": label,
            "value": round(last_val, 2 if key != "btc" else 0),
            "as_of": str(df["date"].iloc[-1].date()),
            "ret_5d": _ret(5), "ret_21d": _ret(21), "ret_63d": _ret(63),
        }
    return indicators


# --------------------------------------------------------------------------
# News filtered to cutoff
# --------------------------------------------------------------------------
NEWS_KEYWORDS = {
    "HUBC": ["HUBCO", "Hub Power", "Hubco", "power", "IPP"],
    "PABC": ["Pak Arab", "PABC", "refinery"],
    "MLCF": ["Maple Leaf", "MLCF", "cement"],
    "OGDC": ["OGDC", "oil and gas development", "Oil & Gas Dev", "gas", "crude"],
    "FABL": ["Faysal", "FABL", "bank"],
    "PPL": ["Pakistan Petroleum", "PPL", "gas", "crude", "brent"],
}


def news_before(cutoff: pd.Timestamp, per_feed: int = 10) -> list[dict]:
    """RSS articles strictly before cutoff+1 day (exclusive on TARGET)."""
    from connectors.rss_news import RssNewsConnector
    r = RssNewsConnector().fetch(per_feed=per_feed)
    if not r.ok:
        return []
    cutoff_plus = cutoff + pd.Timedelta(days=1)
    kept = []
    for a in r.records:
        ts = a.get("published_at")
        if not ts:
            continue
        try:
            pub = pd.to_datetime(ts)
        except (ValueError, TypeError):
            continue
        # Compare date-part: we want articles dated <= cutoff (before TARGET)
        if pub.normalize() <= cutoff:
            kept.append(a)
    return kept


def match_news(articles: list[dict], symbol: str, limit: int = 8) -> list[dict]:
    kw = [w.lower() for w in NEWS_KEYWORDS.get(symbol, [symbol])]
    hits = []
    for a in articles:
        text = ((a.get("title") or "") + " "
                + (a.get("summary") or "")).lower()
        if any(w in text for w in kw):
            hits.append(a)
    if not hits:
        hits = articles  # fall back to market-wide
    return hits[:limit]


# --------------------------------------------------------------------------
# Briefing + Claude call
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a quantitative analyst. Predict the NEXT TRADING
DAY'S CLOSE for a single PSX stock given the data briefing below. You MUST
ground every claim in the briefing; do not invent headlines or numbers.

Output ONLY one valid JSON object, no prose, no fences:
{
  "predicted_close_pkr": <float>,
  "predicted_return_1d_low_pct": <float>,
  "predicted_return_1d_mid_pct": <float>,
  "predicted_return_1d_high_pct": <float>,
  "direction": "UP" | "FLAT" | "DOWN",
  "conviction": "LOW" | "MEDIUM" | "HIGH",
  "rationale": "<2 sentences tying the prediction to specific data points>",
  "key_drivers": ["<one fact>", "<another>"],
  "key_risks": ["<one fact>"]
}

Calibration:
- Typical 1-day moves on PSX are 0.5-2% absolute. A "UP" call should expect
  mid > +0.3%; "DOWN" mid < -0.3%; otherwise FLAT.
- HIGH conviction ONLY when multiple data layers agree (momentum + news +
  macro). Default to MEDIUM or LOW.
- The band [low, high] should usually straddle 0 unless conviction is HIGH."""


def build_briefing(sym: str, tech: dict, macro: dict,
                   articles_sym: list[dict]) -> str:
    def pct(v):
        return "N/A" if v is None else f"{v*100:+.2f}%"
    lines = [
        f"=== {sym} — data as of {CUTOFF.date()} (predicting {TARGET.date()}) ===",
        "",
        "PRICE & TECHNICALS (at cutoff)",
        f"  Yesterday close: {tech.get('close_pkr')} PKR",
        f"  Returns: 1d={pct(tech.get('ret_1d'))}  5d={pct(tech.get('ret_5d'))}  "
        f"21d={pct(tech.get('ret_21d'))}  63d={pct(tech.get('ret_63d'))}",
        f"  Momentum (log): 20d={pct(tech.get('mom_20d_log'))}  "
        f"60d={pct(tech.get('mom_60d_log'))}  "
        f"150d={pct(tech.get('mom_150d_log'))}",
        f"  SMA20={tech.get('sma_20')}  SMA50={tech.get('sma_50')}  "
        f"SMA200={tech.get('sma_200')}  px_vs_sma200={tech.get('px_vs_sma200_pct')}%",
        f"  RSI14={tech.get('rsi_14')}  vol20_ann={tech.get('rvol_20d_ann')}  "
        f"52w_high={tech.get('high_52w')}  dist_from_52w_high={tech.get('dist_from_52w_high_pct')}%",
        "",
        "MACRO AT CUTOFF",
    ]
    for k, v in macro.items():
        lines.append(
            f"  {v.get('label')}: {v.get('value')}  "
            f"5d={pct(v.get('ret_5d'))}  21d={pct(v.get('ret_21d'))}  "
            f"63d={pct(v.get('ret_63d'))}"
        )
    lines += [
        "",
        f"NEWS (published on or before {CUTOFF.date()}, {len(articles_sym)} matched to {sym})",
    ]
    for a in articles_sym:
        lines.append(
            f"  [{a.get('published_at', '?')[:10]}] "
            f"({a.get('source', '?')[:18]}) {a.get('title', '')[:140]}"
        )
    lines += [
        "",
        "FIPI FLOWS: (not available in historical cache — ignore this layer)",
    ]
    return "\n".join(lines)


def predict_claude(briefing: str, sym: str, close: float) -> tuple[dict, str]:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Predict the next-trading-day close for {sym}. "
                f"Yesterday's close: {close} PKR.\n\nDATA:\n{briefing}\n\n"
                f"Return the JSON now."
            ),
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text").strip()
    return parse_json_loose(text), text


def parse_json_loose(s: str) -> dict:
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
    raise ValueError(f"Could not parse JSON: {s[:200]}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
@dataclass
class Row:
    symbol: str
    yesterday_close: float
    predicted_close: float
    predicted_mid_pct: float
    predicted_low_pct: float
    predicted_high_pct: float
    direction: str
    conviction: str
    actual_close: float
    actual_pct: float
    rationale: str
    drivers: list[str]
    risks: list[str]

    @property
    def direction_hit(self) -> bool:
        if self.direction == "UP":
            return self.actual_pct > 0.1
        if self.direction == "DOWN":
            return self.actual_pct < -0.1
        return abs(self.actual_pct) <= 0.5  # FLAT

    @property
    def inside_range(self) -> bool:
        return self.predicted_low_pct <= self.actual_pct <= self.predicted_high_pct

    @property
    def close_error_pct(self) -> float:
        return round((self.predicted_close - self.actual_close) / self.actual_close * 100, 2)


def main():
    print(f"\nWalk-forward nowcast")
    print(f"Data cutoff: {CUTOFF.date()}")
    print(f"Predict    : {TARGET.date()}")
    print(f"Tickers    : {', '.join(TICKERS)}")
    print(f"Model      : claude-haiku-4-5")
    print("-" * 78)

    macro = macro_at_cutoff(CUTOFF)
    print(f"Macro at cutoff: Brent={macro.get('brent', {}).get('value')} "
          f"USD/PKR={macro.get('usdpkr', {}).get('value')}  "
          f"Gold={macro.get('gold', {}).get('value')}")

    print("Fetching news (this hits live RSS, then filtering to <= cutoff)...")
    all_articles = news_before(CUTOFF, per_feed=10)
    print(f"  kept {len(all_articles)} articles published on or before {CUTOFF.date()}")

    rows: list[Row] = []
    for sym in TICKERS:
        path = ROOT / "data" / "ohlcv" / f"{sym}.parquet"
        df = pd.read_parquet(path).sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"])

        hist = df[df["date"] <= CUTOFF]
        target_row = df[df["date"] == TARGET]
        if hist.empty or target_row.empty:
            print(f"[{sym}] missing data, skipping")
            continue

        tech = compute_technicals(hist)
        yesterday_close = float(hist["close"].iloc[-1])
        actual_close = float(target_row["close"].iloc[0])
        actual_pct = round((actual_close / yesterday_close - 1) * 100, 2)

        articles_sym = match_news(all_articles, sym, limit=6)
        briefing = build_briefing(sym, tech, macro, articles_sym)

        print(f"\n[{sym}] yesterday={yesterday_close}  actual today={actual_close}  "
              f"({actual_pct:+.2f}%)  — calling Claude...")
        try:
            pred, _raw = predict_claude(briefing, sym, yesterday_close)
        except Exception as e:
            print(f"  LLM failed: {type(e).__name__}: {e}")
            continue

        row = Row(
            symbol=sym,
            yesterday_close=round(yesterday_close, 2),
            predicted_close=round(float(pred["predicted_close_pkr"]), 2),
            predicted_mid_pct=float(pred.get("predicted_return_1d_mid_pct", 0)),
            predicted_low_pct=float(pred.get("predicted_return_1d_low_pct", 0)),
            predicted_high_pct=float(pred.get("predicted_return_1d_high_pct", 0)),
            direction=pred.get("direction", "FLAT"),
            conviction=pred.get("conviction", "LOW"),
            actual_close=round(actual_close, 2),
            actual_pct=actual_pct,
            rationale=pred.get("rationale", ""),
            drivers=pred.get("key_drivers", []),
            risks=pred.get("key_risks", []),
        )
        rows.append(row)

        hit = "HIT" if row.direction_hit else "MISS"
        in_range = "in-range" if row.inside_range else "out-of-range"
        print(f"  pred close={row.predicted_close}  pred mid={row.predicted_mid_pct:+.2f}%  "
              f"band=[{row.predicted_low_pct:+.2f}%, {row.predicted_high_pct:+.2f}%]  "
              f"actual={row.actual_pct:+.2f}%  {hit}  {in_range}  "
              f"error={row.close_error_pct:+.2f}%")

    # --------------------------------------------------------------------------
    # Summary scorecard
    # --------------------------------------------------------------------------
    print("\n" + "=" * 92)
    print("SCORECARD — 1-day nowcast (Apr 22 close -> Apr 23 close)")
    print("=" * 92)
    header = (f"{'SYM':<6s} {'DIR':<5s} {'CONV':<7s} "
              f"{'Y-CLOSE':>9s} {'PRED':>9s} {'ACTUAL':>9s} "
              f"{'PRED%':>7s} {'ACT%':>7s} {'BAND':>16s} "
              f"{'HIT':>5s} {'RANGE':>8s} {'ERR%':>7s}")
    print(header)
    print("-" * 92)
    dir_hits = 0
    in_range_hits = 0
    abs_errors = []
    for r in rows:
        band = f"[{r.predicted_low_pct:+.1f}, {r.predicted_high_pct:+.1f}]"
        hit_flag = "HIT" if r.direction_hit else "MISS"
        rng_flag = "IN" if r.inside_range else "OUT"
        if r.direction_hit:
            dir_hits += 1
        if r.inside_range:
            in_range_hits += 1
        abs_errors.append(abs(r.close_error_pct))
        print(f"{r.symbol:<6s} {r.direction:<5s} {r.conviction:<7s} "
              f"{r.yesterday_close:>9.2f} {r.predicted_close:>9.2f} "
              f"{r.actual_close:>9.2f} "
              f"{r.predicted_mid_pct:>+7.2f} {r.actual_pct:>+7.2f} "
              f"{band:>16s} {hit_flag:>5s} {rng_flag:>8s} "
              f"{r.close_error_pct:>+7.2f}")

    n = len(rows)
    if n:
        mae = sum(abs_errors) / n
        print("-" * 92)
        print(f"Direction hit rate: {dir_hits}/{n} = {dir_hits/n*100:.0f}%")
        print(f"Inside-range hits:  {in_range_hits}/{n} = {in_range_hits/n*100:.0f}%")
        print(f"Mean absolute close error: {mae:.2f}%")

        # Rationales
        print("\nRATIONALES")
        for r in rows:
            print(f"\n[{r.symbol}]  pred={r.direction}/{r.conviction}  "
                  f"actual={r.actual_pct:+.2f}%  ({'HIT' if r.direction_hit else 'MISS'})")
            print(f"  {r.rationale}")
            if r.drivers:
                print("  Drivers:")
                for d in r.drivers:
                    print(f"    - {d}")
            if r.risks:
                print("  Risks:")
                for rk in r.risks:
                    print(f"    - {rk}")

    # Save result
    out = ROOT / "reports" / "walkforward_today.json"
    out.parent.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cutoff": str(CUTOFF.date()),
        "target": str(TARGET.date()),
        "predictions": [r.__dict__ for r in rows],
        "summary": {
            "direction_hits": dir_hits,
            "direction_n": n,
            "inside_range_hits": in_range_hits,
            "mean_abs_close_error_pct": round(sum(abs_errors) / n, 2) if n else None,
        },
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
