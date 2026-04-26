"""Today's buy recommendation — filter BULLISH picks, compute smart entry
zones, rank by expected return * conviction. Output: a concrete trade plan
for holding through the next ~5 trading days."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from config.costs import (MINIMUM_NET_EDGE_PCT, minimum_gross_for_trade,
                            net_return_pct, round_trip_cost_pct,
                            trade_is_viable)

LOG = ROOT / "data" / "predictions_log.json"
CONVICTION_WEIGHT = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.3}
RT_COST = round_trip_cost_pct()
MIN_GROSS = minimum_gross_for_trade()


def load_predictions() -> list[dict]:
    return json.loads(LOG.read_text(encoding="utf-8"))["predictions"]


def recent_support(symbol: str) -> dict:
    """Recent swing low, SMA20, last-week low — used to floor the buy zone."""
    df = pd.read_parquet(ROOT / "data" / "ohlcv" / f"{symbol}.parquet")
    df = df.sort_values("date").tail(60).reset_index(drop=True)
    close = df["close"].astype(float)
    return {
        "sma_20": round(float(close.tail(20).mean()), 2),
        "low_5d": round(float(close.tail(5).min()), 2),
        "low_20d": round(float(close.tail(20).min()), 2),
    }


def smart_entry_zone(entry: float, stop: float, support: dict,
                     direction: str) -> tuple[float, float]:
    """Good-till-cancelled LIMIT BUY band:
      - upper: entry (don't chase above yesterday's close)
      - lower: max(stop+2%, 97.5% of entry, recent 5d low, 98% of SMA20)
    """
    if direction != "BULLISH":
        return (entry, entry)
    floor_1 = entry * 0.975          # 2.5% below yesterday's close
    floor_2 = support["low_5d"]      # 5-day swing low
    floor_3 = support["sma_20"] * 0.99
    floor_4 = stop * 1.02            # don't buy within 2% of the stop
    lower = max(floor_1, floor_2, floor_3, floor_4)
    # Ensure lower is actually below entry
    if lower >= entry * 0.998:
        lower = entry * 0.985
    return (round(lower, 2), round(entry, 2))


def score(pred: dict) -> float:
    """Rank buys by expected NET return * conviction weight.

    Filters out:
      - non-BUY/ADD or non-BULLISH calls
      - trades whose gross mid return doesn't clear cost+edge
      - stocks in earnings blackout (≤5 days, HIGH/MED confidence) — we
        don't want to take new positions into a result-day gap.
    """
    if pred.get("suggested_action") not in ("BUY", "ADD"):
        return -99
    if pred.get("direction") != "BULLISH":
        return -99
    mid_gross = float(pred.get("expected_return_5d_mid_pct") or 0)
    viable, _ = trade_is_viable(mid_gross)
    if not viable:
        return -99
    # Earnings blackout filter
    try:
        from brain.earnings_calendar import next_event
        ev = next_event(pred["symbol"])
        if ev.get("in_blackout_5d"):
            return -99
    except Exception:
        pass
    w = CONVICTION_WEIGHT.get(pred.get("conviction", "LOW"), 0)
    mid_net = net_return_pct(mid_gross)
    return w * mid_net


def risk_reward(entry_low: float, entry_high: float,
                stop: float, target: float) -> dict:
    """At the midpoint of the entry band, compute R:R."""
    entry = (entry_low + entry_high) / 2
    if entry <= 0 or stop >= entry:
        return {"r_pct": 0, "reward_pct": 0, "rr": 0}
    risk = (entry - stop) / entry * 100
    reward = (target - entry) / entry * 100
    return {
        "entry_mid": round(entry, 2),
        "risk_pct": round(risk, 2),
        "reward_pct": round(reward, 2),
        "rr": round(reward / risk, 2) if risk > 0 else 0,
    }


def main():
    preds = load_predictions()
    ranked = sorted(preds, key=score, reverse=True)

    print("=" * 100)
    print(f"TODAY'S BUY LIST  (data cutoff: {preds[0]['data_snapshot']['as_of_price_date']})")
    print(f"Horizon: {preds[0]['horizon_trading_days']} trading days (roughly next week)")
    print(f"Model: {preds[0]['model']}   Universe: {len(preds)} stocks")
    print(f"Costs: round-trip = {RT_COST:.2f}%  |  min gross for trade = "
          f"{MIN_GROSS:.2f}%  (cost + {MINIMUM_NET_EDGE_PCT:.1f}% edge)")
    print("=" * 100)

    buys = [p for p in ranked if score(p) > 0]
    hold = [p for p in ranked if p.get("suggested_action") == "HOLD"]
    avoids = [p for p in ranked if p.get("suggested_action") in ("AVOID", "SELL", "TRIM")]
    # Trades suggested BUY/ADD by LLM but filtered out by cost model
    sub_edge = [p for p in ranked
                 if p.get("suggested_action") in ("BUY", "ADD")
                 and p.get("direction") == "BULLISH"
                 and score(p) < 0]

    print(f"\n{len(buys)} TAKE  |  {len(sub_edge)} LLM-BUY-BUT-SUB-EDGE  |  "
          f"{len(hold)} HOLD  |  {len(avoids)} AVOID/TRIM\n")

    if sub_edge:
        print("-" * 100)
        print("LLM suggested BUY/ADD but gross expected return < "
              f"{MIN_GROSS:.2f}% threshold — SKIP:")
        for p in sub_edge:
            g = float(p.get("expected_return_5d_mid_pct") or 0)
            n = net_return_pct(g)
            print(f"  {p['symbol']:<6s} gross={g:+.2f}%  net(cost+CGT)={n:+.2f}%  "
                  f"conviction={p['conviction']}")
        print()

    # Earnings-blackout report (informational)
    try:
        from brain.earnings_calendar import universe_calendar
        cal = universe_calendar(days_ahead=14)
        blackouts = cal.get("blackout_now") or []
        if blackouts:
            print("-" * 100)
            print("EARNINGS BLACKOUT (≤5 trading days, HIGH/MED confidence) "
                  "— NO new BUY/ADD on these:")
            for ev in blackouts:
                print(f"  {ev['symbol']:<6s} reports {ev['next_event_date_utc']}  "
                      f"({ev['days_until']}d, conf={ev['confidence']}, "
                      f"src={ev['source']})")
            print()
    except Exception:
        pass

    # --------------------------------------------------------------
    # BUY LIST — the actionable part
    # --------------------------------------------------------------
    if not buys:
        print("No BULLISH MEDIUM/HIGH setups today. Staying in cash is a valid answer.")
        return

    print("-" * 115)
    print(f"{'#':<3s} {'SYM':<6s} {'SECT':<12s} "
          f"{'Y-CLS':>7s} {'ENTRY BAND':>15s} {'STOP':>7s} {'TARGET':>7s} "
          f"{'GROSS':>6s} {'COST':>5s} {'NET':>6s} {'R:R':>5s} {'CONV':>6s} "
          f"{'SCORE':>6s}")
    print("-" * 115)
    trade_plans = []
    for i, p in enumerate(buys, 1):
        sym = p["symbol"]
        sup = recent_support(sym)
        entry_px = float(p["entry_price_pkr"])
        stop = float(p["suggested_stop_pkr"])
        target = float(p["suggested_target_pkr"])
        band = smart_entry_zone(entry_px, stop, sup, p["direction"])
        rr = risk_reward(band[0], band[1], stop, target)
        gross_mid = float(p.get("expected_return_5d_mid_pct") or 0)
        net_mid = net_return_pct(gross_mid)

        sector = (p.get("sector") or "?")[:12]
        print(f"{i:<3d} {sym:<6s} {sector:<12s} "
              f"{entry_px:>7.2f} [{band[0]:>6.2f}, {band[1]:>6.2f}] "
              f"{stop:>7.2f} {target:>7.2f} "
              f"{gross_mid:>+6.2f} {RT_COST:>+5.2f} {net_mid:>+6.2f} "
              f"{rr['rr']:>5.2f} {p['conviction']:>6s} "
              f"{score(p):>+6.2f}")

        trade_plans.append({
            "symbol": sym, "sector": p.get("sector"),
            "y_close": entry_px,
            "buy_low": band[0], "buy_high": band[1],
            "entry_mid": rr["entry_mid"],
            "stop": stop, "target": target,
            "risk_pct": rr["risk_pct"], "reward_pct": rr["reward_pct"],
            "rr": rr["rr"],
            "gross_return_5d_pct": gross_mid,
            "round_trip_cost_pct": RT_COST,
            "net_return_5d_pct": net_mid,
            "conviction": p["conviction"],
            "rationale": p["rationale"],
            "drivers": p.get("key_drivers", []),
            "risks": p.get("key_risks", []),
            "support": sup,
        })

    # --------------------------------------------------------------
    # Detailed trade plans
    # --------------------------------------------------------------
    print("\n" + "=" * 100)
    print("DETAILED TRADE PLANS")
    print("=" * 100)
    for plan in trade_plans:
        print(f"\n[{plan['symbol']} - {plan['sector']}]  "
              f"{plan['conviction']} conviction")
        print(f"  Rationale: {plan['rationale']}")
        print(f"  HOW TO ENTER:")
        print(f"    Place a LIMIT BUY at {plan['buy_low']}-{plan['buy_high']} PKR "
              f"(yesterday close = {plan['y_close']}).")
        print(f"    Mid-entry: {plan['entry_mid']} PKR.  "
              f"Avoid chasing above {plan['y_close']}.")
        print(f"  SUPPORT CHECKS:  "
              f"5d_low={plan['support']['low_5d']}  "
              f"20d_low={plan['support']['low_20d']}  "
              f"SMA20={plan['support']['sma_20']}")
        print(f"  STOP LOSS:  {plan['stop']} PKR  "
              f"(risk {plan['risk_pct']}% from mid)")
        print(f"  TARGET:     {plan['target']} PKR  "
              f"(gross reward {plan['reward_pct']}%, "
              f"net after costs+CGT = "
              f"{net_return_pct(plan['reward_pct']):+.2f}%, "
              f"R:R gross = {plan['rr']})")
        print(f"  5D EXPECTED: gross={plan['gross_return_5d_pct']:+.2f}%, "
              f"round-trip cost={plan['round_trip_cost_pct']:.2f}%, "
              f"net={plan['net_return_5d_pct']:+.2f}%")
        if plan["drivers"]:
            print(f"  Drivers:")
            for d in plan["drivers"][:3]:
                print(f"    + {d}")
        if plan["risks"]:
            print(f"  Risks:")
            for r in plan["risks"][:3]:
                print(f"    - {r}")

    # --------------------------------------------------------------
    # Portfolio construction
    # --------------------------------------------------------------
    total_score = sum(score(p) for p in buys)
    if total_score > 0:
        print("\n" + "=" * 100)
        print(f"SUGGESTED PORTFOLIO (if allocating all capital to these {len(buys)} names)")
        print("=" * 100)
        print(f"{'#':<3s} {'SYM':<6s} {'WEIGHT':>8s}  {'If you invest 100,000 PKR':>28s}")
        print("-" * 60)
        capital = 100_000
        for i, p in enumerate(buys, 1):
            w = score(p) / total_score
            alloc = capital * w
            sym = p["symbol"]
            entry_mid = (float(p["entry_price_pkr"]) +
                         smart_entry_zone(
                             float(p["entry_price_pkr"]),
                             float(p["suggested_stop_pkr"]),
                             recent_support(sym),
                             p["direction"])[0]) / 2
            shares = int(alloc / entry_mid) if entry_mid > 0 else 0
            print(f"{i:<3d} {sym:<6s} {w*100:>7.1f}%  "
                  f"alloc={alloc:>8,.0f} PKR  ~{shares} shares @ ~{entry_mid:.2f}")

    # Save
    out = ROOT / "reports" / "todays_buys.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(trade_plans, indent=2, default=str),
                   encoding="utf-8")
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
