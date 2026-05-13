"""Score the rule-based + LLM predictions log against actual forward returns.

Reads `data/predictions_log.json` (15 generation dates, 447 predictions
across 35 symbols, 3 models). For each prediction with a `prediction_id`
of the form `<gen_date>-<symbol>` and a `horizon_trading_days`, looks up
the actual close-to-close return from OHLCV, then scores:

  * Direction accuracy (BULLISH = positive fwd, BEARISH = negative)
  * Action P&L (BUY = full long, ADD = 0.75, HOLD = 0.5, AVOID = 0)
  * Conviction calibration (HIGH should out-perform MEDIUM should
    out-perform LOW)
  * Per-symbol track record
  * Per-model comparison

Output:
  data/_research/predictions_score.json
  data/_research/predictions_score.md
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

LOG_PATH = ROOT / "data" / "predictions_log.json"
OHLCV_DIR = ROOT / "data" / "ohlcv"
OUT_DIR = ROOT / "data" / "_research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fwd_return(symbol: str, gen_date: date, days: int) -> float | None:
    fp = OHLCV_DIR / f"{symbol.upper()}.parquet"
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    if "close" not in df.columns or "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    anchor = df[df["date"] <= gen_date]
    future = df[df["date"] > gen_date]
    if anchor.empty or len(future) < days:
        return None
    c0 = float(anchor.iloc[-1]["close"])
    c1 = float(future.iloc[days - 1]["close"])
    if c0 <= 0:
        return None
    return c1 / c0 - 1.0


BUCKET_LONG = {
    "BUY": 1.0, "ADD": 0.75, "HOLD": 0.50,
    "WATCH": 0.25, "AVOID": 0.0, "TRIM": 0.0,
}


def main() -> int:
    log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    preds = log.get("predictions") or []
    print(f"[score] {len(preds)} predictions in log")

    scored = []
    n_skipped = 0
    for p in preds:
        sym = p.get("symbol")
        gen_at = p.get("generated_at")
        if not (sym and gen_at):
            n_skipped += 1
            continue
        try:
            gen_date = datetime.fromisoformat(gen_at[:10]).date()
        except Exception:
            n_skipped += 1
            continue
        h = int(p.get("horizon_trading_days") or 5)
        actual = fwd_return(sym, gen_date, h)
        if actual is None:
            n_skipped += 1
            continue

        pred_dir = (p.get("direction") or "").upper()
        pred_act = (p.get("suggested_action") or "").upper()
        conv     = (p.get("conviction") or "").upper()
        model    = p.get("model") or "?"
        sector   = p.get("sector") or "?"

        # Direction accuracy: BULLISH = fwd>0.5%, BEARISH = fwd<-0.5%
        if pred_dir == "BULLISH":
            dir_correct = actual > 0.005
        elif pred_dir == "BEARISH":
            dir_correct = actual < -0.005
        elif pred_dir == "NEUTRAL":
            dir_correct = abs(actual) < 0.04
        else:
            dir_correct = None

        # Action P&L: assume per-stock 1% notional; weight by bucket
        long_frac = BUCKET_LONG.get(pred_act, 0.5)
        action_pnl = long_frac * actual

        # Edge over neutral hold (HOLD = 0.5 long)
        edge_pnl = (long_frac - 0.5) * actual

        scored.append({
            "as_of":        gen_date.isoformat(),
            "symbol":       sym,
            "sector":       sector,
            "model":        model,
            "horizon":      h,
            "direction":    pred_dir,
            "action":       pred_act,
            "conviction":   conv,
            "actual_pct":   actual * 100,
            "dir_correct":  dir_correct,
            "action_pnl":   action_pnl * 100,
            "edge_pnl":     edge_pnl * 100,
        })

    print(f"[score] scored {len(scored)} (skipped {n_skipped})")

    if not scored:
        return 1

    # ---- Aggregates -----------------------------------------------------
    # Per-direction
    by_dir: dict[str, list[float]] = defaultdict(list)
    by_dir_correct: dict[str, list[bool]] = defaultdict(list)
    for s in scored:
        by_dir[s["direction"]].append(s["actual_pct"])
        if s["dir_correct"] is not None:
            by_dir_correct[s["direction"]].append(s["dir_correct"])
    dir_table = []
    for d, rs in sorted(by_dir.items()):
        n = len(rs)
        avg = mean(rs)
        hit = sum(by_dir_correct[d]) / max(len(by_dir_correct[d]), 1)
        dir_table.append({"direction": d, "n": n,
                          "avg_actual_pct": avg, "hit_rate": hit})

    # Per-action
    by_act: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_act[s["action"]].append(s)
    act_table = []
    for a, items in sorted(by_act.items()):
        n = len(items)
        avg_actual = mean(it["actual_pct"] for it in items)
        avg_edge   = mean(it["edge_pnl"]   for it in items)
        act_table.append({"action": a, "n": n,
                          "avg_actual_pct": avg_actual,
                          "avg_edge_vs_hold_pct": avg_edge})

    # Per-model
    by_model: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_model[s["model"]].append(s)
    model_table = []
    for m, items in sorted(by_model.items()):
        n = len(items)
        hit_pct = sum(1 for it in items if it["dir_correct"]) / n * 100
        avg_action_pnl = mean(it["action_pnl"] for it in items)
        avg_edge   = mean(it["edge_pnl"]   for it in items)
        model_table.append({
            "model": m, "n": n,
            "dir_hit_pct": hit_pct,
            "avg_action_pnl_pct": avg_action_pnl,
            "avg_edge_vs_hold_pct": avg_edge,
        })

    # Per-conviction (calibration)
    by_conv: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_conv[s["conviction"]].append(s)
    conv_table = []
    for c, items in sorted(by_conv.items()):
        n = len(items)
        # for BULLISH+HIGH: average actual return should be highest
        bull = [it for it in items if it["direction"] == "BULLISH"]
        bear = [it for it in items if it["direction"] == "BEARISH"]
        bull_avg = mean(it["actual_pct"] for it in bull) if bull else None
        bear_avg = mean(it["actual_pct"] for it in bear) if bear else None
        conv_table.append({
            "conviction": c, "n": n,
            "n_bullish": len(bull),
            "bullish_avg_actual_pct": bull_avg,
            "n_bearish": len(bear),
            "bearish_avg_actual_pct": bear_avg,
        })

    # Per-symbol track
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for s in scored:
        by_sym[s["symbol"]].append(s)
    sym_table = []
    for sym, items in sorted(by_sym.items()):
        n = len(items)
        n_correct = sum(1 for it in items if it["dir_correct"])
        avg_action_pnl = mean(it["action_pnl"] for it in items)
        sym_table.append({
            "symbol": sym, "n": n,
            "dir_hit_pct": n_correct / n * 100,
            "avg_action_pnl_pct": avg_action_pnl,
        })
    sym_table.sort(key=lambda r: r["avg_action_pnl_pct"], reverse=True)

    out_payload = {
        "n_scored": len(scored),
        "by_direction": dir_table,
        "by_action": act_table,
        "by_model": model_table,
        "by_conviction": conv_table,
        "by_symbol_top10_alpha": sym_table[:10],
        "by_symbol_bot10_alpha": sym_table[-10:],
    }
    (OUT_DIR / "predictions_score.json").write_text(
        json.dumps(out_payload, indent=2, default=str), encoding="utf-8")

    md = ["# Predictions log scoreboard", ""]
    md.append(f"Scored {len(scored)} predictions across "
              f"{len(by_sym)} symbols, {len(by_model)} models")
    md.append("")

    md.append("## Direction accuracy")
    md.append("| Direction | n | avg actual % | hit % |")
    md.append("|-----------|---|--------------|-------|")
    for r in dir_table:
        md.append(f"| {r['direction'] or '?':<8} | {r['n']:>3} | "
                  f"{r['avg_actual_pct']:+5.2f}% | "
                  f"{r['hit_rate']*100:>4.0f}% |")
    md.append("")

    md.append("## Action P&L (vs HOLD baseline)")
    md.append("| Action | n | avg actual % | edge vs HOLD |")
    md.append("|--------|---|--------------|--------------|")
    for r in act_table:
        md.append(f"| {r['action'] or '?':<6} | {r['n']:>3} | "
                  f"{r['avg_actual_pct']:+5.2f}% | "
                  f"{r['avg_edge_vs_hold_pct']:+5.2f}% |")
    md.append("")

    md.append("## Model comparison")
    md.append("| Model | n | dir hit % | action P&L | edge vs HOLD |")
    md.append("|-------|---|-----------|------------|--------------|")
    for r in model_table:
        md.append(f"| {r['model']:<22} | {r['n']:>3} | "
                  f"{r['dir_hit_pct']:>4.1f}% | "
                  f"{r['avg_action_pnl_pct']:+.2f}% | "
                  f"{r['avg_edge_vs_hold_pct']:+.2f}% |")
    md.append("")

    md.append("## Conviction calibration (BULLISH actual return by conviction)")
    md.append("| Conviction | n | n bullish | bull avg | n bearish | bear avg |")
    md.append("|------------|---|-----------|----------|-----------|----------|")
    for r in conv_table:
        b = r['bullish_avg_actual_pct']
        be = r['bearish_avg_actual_pct']
        md.append(f"| {r['conviction'] or '?':<8} | {r['n']:>3} | "
                  f"{r['n_bullish']:>3} | "
                  f"{(f'{b:+5.2f}%' if b is not None else '   -   ')} | "
                  f"{r['n_bearish']:>3} | "
                  f"{(f'{be:+5.2f}%' if be is not None else '   -   ')} |")
    md.append("")

    md.append("## Top 10 symbols by avg action P&L")
    md.append("| Symbol | n | dir hit % | avg P&L |")
    md.append("|--------|---|-----------|---------|")
    for r in sym_table[:10]:
        md.append(f"| {r['symbol']:<6} | {r['n']:>3} | "
                  f"{r['dir_hit_pct']:>4.1f}% | "
                  f"{r['avg_action_pnl_pct']:+.2f}% |")
    md.append("")
    md.append("## Bottom 10 symbols by avg action P&L")
    md.append("| Symbol | n | dir hit % | avg P&L |")
    md.append("|--------|---|-----------|---------|")
    for r in sym_table[-10:]:
        md.append(f"| {r['symbol']:<6} | {r['n']:>3} | "
                  f"{r['dir_hit_pct']:>4.1f}% | "
                  f"{r['avg_action_pnl_pct']:+.2f}% |")
    md.append("")

    (OUT_DIR / "predictions_score.md").write_text(
        "\n".join(md), encoding="utf-8")
    print("\nDirection accuracy:")
    for r in dir_table:
        print(f"  {r['direction'] or '?':<8} n={r['n']:>3}  "
              f"avg_actual={r['avg_actual_pct']:+5.2f}%  hit={r['hit_rate']*100:>4.0f}%")
    print("\nModel comparison:")
    for r in model_table:
        print(f"  {r['model']:<24} n={r['n']:>3}  "
              f"hit={r['dir_hit_pct']:>4.1f}%  "
              f"action_pnl={r['avg_action_pnl_pct']:+.2f}%  "
              f"edge_vs_hold={r['avg_edge_vs_hold_pct']:+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
