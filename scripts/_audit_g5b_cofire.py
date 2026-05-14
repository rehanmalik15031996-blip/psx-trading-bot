"""Gap-5 v2: BTC-vs-gold redundancy on the full BTC + gold history,
without requiring KSE-100 forward returns. The redundancy question
is purely: how often do these tags fire together?

Run on every trading-day-equivalent date (using gold's date series
as the calendar). For each, compute btc_off / btc_on / gold_up /
gold_down predicates and tabulate.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

MACRO = ROOT / "data" / "macro"


def _load(name: str) -> pd.DataFrame:
    df = pd.read_parquet(MACRO / f"{name}.parquet")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _ret(df: pd.DataFrame, col: str, days: int) -> pd.Series:
    return df[col] / df[col].shift(days) - 1


def main():
    btc = _load("btc")
    gold = _load("gold")
    btc_col  = "value" if "value" in btc.columns else "close"
    gold_col = "value" if "value" in gold.columns else "close"

    btc["b5"]  = _ret(btc, btc_col, 5)
    btc["b21"] = _ret(btc, btc_col, 21)
    gold["g21"] = _ret(gold, gold_col, 21)

    # Inner join on date so we look only at days where BOTH have data
    merged = btc[["date","b5","b21"]].merge(
        gold[["date","g21"]], on="date", how="inner")
    merged = merged.dropna(subset=["b5","b21","g21"]).reset_index(drop=True)
    print(f"Joint days with both BTC and gold returns: {len(merged)}")
    if not len(merged):
        print("No data — abort.")
        return 1
    print(f"Date range: {merged['date'].iloc[0]} .. "
          f"{merged['date'].iloc[-1]}")

    merged["btc_off"]   = (merged["b5"]  <= -0.12) | (merged["b21"] <= -0.20)
    merged["btc_on"]    = (merged["b5"]  >=  0.15) | (merged["b21"] >=  0.25)
    merged["gold_up"]   = merged["g21"] >=  0.05
    merged["gold_down"] = merged["g21"] <= -0.05

    n = len(merged)
    print()
    print("Fire counts (full daily history):")
    for col in ["btc_off","btc_on","gold_up","gold_down"]:
        c = int(merged[col].sum())
        print(f"  {col:<12s}: {c:>4} days ({c/n*100:.1f}% of {n})")

    print()
    print("Joint distribution (btc_off, gold_up):")
    for bo in [False, True]:
        for gu in [False, True]:
            cnt = int(((merged["btc_off"] == bo) &
                       (merged["gold_up"] == gu)).sum())
            print(f"  btc_off={bo}, gold_up={gu}: {cnt:>4}")
    print()
    print("Joint distribution (btc_off, gold_down):")
    for bo in [False, True]:
        for gd in [False, True]:
            cnt = int(((merged["btc_off"] == bo) &
                       (merged["gold_down"] == gd)).sum())
            print(f"  btc_off={bo}, gold_down={gd}: {cnt:>4}")

    # P(gold_up | btc_off) and the reverse
    if merged["btc_off"].sum():
        p_gu_given_bo = ((merged["btc_off"] & merged["gold_up"]).sum() /
                          merged["btc_off"].sum())
        p_gd_given_bo = ((merged["btc_off"] & merged["gold_down"]).sum() /
                          merged["btc_off"].sum())
    else:
        p_gu_given_bo = p_gd_given_bo = 0.0
    if merged["gold_up"].sum():
        p_bo_given_gu = ((merged["btc_off"] & merged["gold_up"]).sum() /
                          merged["gold_up"].sum())
    else:
        p_bo_given_gu = 0.0

    print()
    print("Conditional rates:")
    print(f"  P(gold_up   | btc_off) = {p_gu_given_bo*100:.1f}%")
    print(f"  P(gold_down | btc_off) = {p_gd_given_bo*100:.1f}%")
    print(f"  P(btc_off   | gold_up) = {p_bo_given_gu*100:.1f}%")

    # Days where btc_off fires but gold is NEUTRAL (neither up nor down)
    btc_alone = int((merged["btc_off"] &
                      ~merged["gold_up"] & ~merged["gold_down"]).sum())
    gold_alone = int(((~merged["btc_off"]) &
                       (merged["gold_up"] | merged["gold_down"])).sum())
    print()
    print(f"Days btc_off fires when gold is neutral: {btc_alone}")
    print(f"Days gold fires when btc is neutral:     {gold_alone}")

    print()
    print("=" * 80)
    print("VERDICT")
    print("=" * 80)
    if merged["btc_off"].sum() < 10:
        print("[INERT] btc_risk_off fires too rarely (n<10) over 5y to "
              "evaluate redundancy. Driver is mostly dormant; harmless.")
    elif p_gu_given_bo >= 0.70:
        print(f"[REDUNDANT] {p_gu_given_bo*100:.0f}% of btc_off fires "
              "co-occur with gold_up. Driver is essentially a noisy "
              "duplicate of gold_up. REMOVE or set Banking/Cement "
              "weights to 0.")
    elif btc_alone >= 5:
        print(f"[INDEPENDENT] btc_off fires {btc_alone} times WITHOUT "
              "either gold tag. Has real independent signal in 5y data.")
    else:
        print("[SPARSE_INDEPENDENT] btc_off has some independent fires "
              "but too few to call meaningful. Hold.")

    out = ROOT / "data" / "_research" / "btc_gold_cofire.json"
    out.write_text(json.dumps({
        "n_joint_days": int(n),
        "fire_counts": {col: int(merged[col].sum())
                         for col in ["btc_off","btc_on","gold_up","gold_down"]},
        "p_gold_up_given_btc_off":   p_gu_given_bo,
        "p_gold_down_given_btc_off": p_gd_given_bo,
        "p_btc_off_given_gold_up":   p_bo_given_gu,
        "btc_alone_when_gold_neutral": btc_alone,
        "gold_alone_when_btc_neutral": gold_alone,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
