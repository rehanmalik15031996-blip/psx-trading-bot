"""Gap-5: is the new `btc_risk_off` / `btc_risk_on` driver redundant
with `gold_up` / `gold_down`?

We sweep 5 years of macro parquets and compute, for every Friday:
  - whether btc_risk_off fires (BTC ret_5d <= -12% OR ret_21d <= -20%)
  - whether btc_risk_on fires
  - whether gold_up fires (gold ret_21d >= +0.05, mirrors macro_impact)
  - whether gold_down fires (gold ret_21d <= -0.05)

Then compute:
  1. Co-firing rate: P(btc_risk_off | gold_up) and P(gold_up | btc_risk_off)
  2. Marginal predictive value: among days when BTC fires but gold does
     NOT (and vice versa), what's the next-week KSE-100 forward return?
  3. Days when btc_risk_off fires alone -> does the universe fall more
     than baseline? If yes, BTC adds value above gold.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

MACRO = ROOT / "data" / "macro"
KSE100 = MACRO / "kse100.parquet"


def _load(name: str) -> pd.DataFrame:
    p = MACRO / f"{name}.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def _ret(df: pd.DataFrame, d: date, days: int, col: str) -> float | None:
    if col not in df.columns:
        return None
    sub = df[df["date"] <= d]
    if len(sub) < days + 1:
        return None
    cur = sub.iloc[-1][col]
    prev = sub.iloc[-(days+1)][col]
    if not (pd.notna(cur) and pd.notna(prev)) or prev == 0:
        return None
    return (cur / prev) - 1


def main() -> int:
    btc = _load("btc")
    gold = _load("gold")
    kse  = _load("kse100")
    print(f"BTC rows {len(btc)}  cols {list(btc.columns)}")
    print(f"Gold rows {len(gold)}  cols {list(gold.columns)}")
    print(f"KSE100 rows {len(kse)}  cols {list(kse.columns)}")

    # Pick the close/value column dynamically
    btc_col  = next((c for c in ["close", "value", "btc_close"]
                     if c in btc.columns), None)
    gold_col = next((c for c in ["close", "value", "gold_close"]
                     if c in gold.columns), None)
    kse_col  = next((c for c in ["close", "kse100_close", "value"]
                     if c in kse.columns), None)
    if not all([btc_col, gold_col, kse_col]):
        print(f"Missing column: btc={btc_col} gold={gold_col} kse={kse_col}")
        return 1

    # Walk Fridays from 2021-06 to 2026-04 (avoid Phase F window)
    start = date(2021, 6, 4)
    end   = date(2026, 4, 30)
    dates = []
    d = start
    while d <= end:
        if d.weekday() == 4:
            dates.append(d)
        d += timedelta(days=1)
    print(f"Fridays: {len(dates)}")

    rows = []
    for d in dates:
        b5  = _ret(btc, d, 5, btc_col)
        b21 = _ret(btc, d, 21, btc_col)
        g21 = _ret(gold, d, 21, gold_col)
        # KSE-100 forward 5d
        kse_after = kse[kse["date"] > d]
        kse_at    = kse[kse["date"] == d]
        kse_fwd5  = None
        if not kse_at.empty and len(kse_after) >= 5:
            t0 = float(kse_at.iloc[0][kse_col])
            t5 = float(kse_after.iloc[4][kse_col])
            if t0 > 0:
                kse_fwd5 = (t5 - t0) / t0 * 100
        if b5 is None or b21 is None or g21 is None or kse_fwd5 is None:
            continue
        btc_off = (b5 <= -0.12) or (b21 <= -0.20)
        btc_on  = (b5 >= 0.15) or (b21 >= 0.25)
        # Mirror macro_impact gold thresholds
        gold_up   = g21 >= 0.05
        gold_down = g21 <= -0.05
        rows.append({
            "date": d.isoformat(),
            "b5": round(b5, 4), "b21": round(b21, 4),
            "g21": round(g21, 4),
            "btc_off": btc_off, "btc_on": btc_on,
            "gold_up": gold_up, "gold_down": gold_down,
            "kse_fwd5_pct": round(kse_fwd5, 3),
        })

    n = len(rows)
    print(f"Evaluated {n} weekly rows")

    # Counts
    n_btc_off = sum(1 for r in rows if r["btc_off"])
    n_btc_on  = sum(1 for r in rows if r["btc_on"])
    n_gold_up = sum(1 for r in rows if r["gold_up"])
    n_gold_dn = sum(1 for r in rows if r["gold_down"])
    print()
    print(f"Marginal fire counts:")
    print(f"  btc_risk_off:  {n_btc_off:>3} ({n_btc_off/n*100:.1f}%)")
    print(f"  btc_risk_on:   {n_btc_on:>3} ({n_btc_on/n*100:.1f}%)")
    print(f"  gold_up:       {n_gold_up:>3} ({n_gold_up/n*100:.1f}%)")
    print(f"  gold_down:     {n_gold_dn:>3} ({n_gold_dn/n*100:.1f}%)")

    # Co-fire
    n_off_and_up    = sum(1 for r in rows if r["btc_off"] and r["gold_up"])
    n_off_no_up     = sum(1 for r in rows if r["btc_off"] and not r["gold_up"])
    n_no_off_and_up = sum(1 for r in rows if not r["btc_off"] and r["gold_up"])

    print()
    print("Co-firing (the redundancy test):")
    if n_btc_off:
        print(f"  P(gold_up | btc_off) = {n_off_and_up}/{n_btc_off} = "
              f"{n_off_and_up/n_btc_off*100:.0f}%")
    if n_gold_up:
        print(f"  P(btc_off | gold_up) = {n_off_and_up}/{n_gold_up} = "
              f"{n_off_and_up/n_gold_up*100:.0f}%")
    print(f"  btc_off only (without gold_up): {n_off_no_up}")
    print(f"  gold_up only (without btc_off): {n_no_off_and_up}")

    # Marginal predictive value: among the 4 cells of the
    # (btc_off, gold_up) 2x2, what's the next-5d KSE-100 mean?
    def _mean(filt) -> tuple[int, float]:
        vals = [r["kse_fwd5_pct"] for r in rows if filt(r)]
        return (len(vals), sum(vals)/len(vals) if vals else 0)

    print()
    print(f"Forward-5d KSE-100 mean conditional on each combo:")
    n_all, m_all = _mean(lambda r: True)
    n00, m00 = _mean(lambda r: not r["btc_off"] and not r["gold_up"])
    n01, m01 = _mean(lambda r: not r["btc_off"] and     r["gold_up"])
    n10, m10 = _mean(lambda r:     r["btc_off"] and not r["gold_up"])
    n11, m11 = _mean(lambda r:     r["btc_off"] and     r["gold_up"])
    print(f"  full sample baseline:     mean={m_all:+.2f}%  n={n_all}")
    print(f"  btc_off=N, gold_up=N:     mean={m00:+.2f}%  n={n00}")
    print(f"  btc_off=N, gold_up=Y:     mean={m01:+.2f}%  n={n01}")
    print(f"  btc_off=Y, gold_up=N:     mean={m10:+.2f}%  n={n10}")
    print(f"  btc_off=Y, gold_up=Y:     mean={m11:+.2f}%  n={n11}")

    print()
    print("=" * 80)
    print("VERDICT")
    print("=" * 80)
    if n_btc_off == 0:
        print("[INERT] btc_risk_off never fired in 5 years — driver is "
              "dormant, neither adds nor harms.")
    else:
        cofire_pct = (n_off_and_up / n_btc_off * 100) if n_btc_off else 0
        # Marginal value of btc_off when gold_up is OFF: is m10 < m00?
        marginal_when_no_gold = m10 - m00
        marginal_when_gold    = m11 - m01
        print(f"Marginal forward-5d effect of btc_off (when gold_up=N): "
              f"{marginal_when_no_gold:+.2f}pp")
        print(f"Marginal forward-5d effect of btc_off (when gold_up=Y): "
              f"{marginal_when_gold:+.2f}pp")
        if cofire_pct >= 70 and marginal_when_no_gold > -0.5:
            print(f"[REDUNDANT] btc_off co-fires with gold_up "
                  f"{cofire_pct:.0f}% of the time AND its marginal effect "
                  "when gold isn't elevated is small. The driver is "
                  "noise/redundant — recommend REMOVE or lower its "
                  "Banking/Cement weights to 0.")
        elif marginal_when_no_gold <= -0.5 and n10 >= 3:
            print(f"[ADDITIVE] btc_off has independent predictive value: "
                  f"when it fires WITHOUT gold_up (n={n10}), next-week "
                  f"KSE-100 averages {m10:+.2f}% vs {m00:+.2f}% baseline "
                  f"({marginal_when_no_gold:+.2f}pp signal). KEEP.")
        else:
            print(f"[WEAK] btc_off has limited independent signal "
                  f"(n_alone={n10}, marginal {marginal_when_no_gold:+.2f}pp).")

    out = ROOT / "data" / "_research" / "btc_vs_gold_redundancy.json"
    out.write_text(json.dumps({
        "n_rows": n,
        "counts": {"btc_off": n_btc_off, "btc_on": n_btc_on,
                    "gold_up": n_gold_up, "gold_down": n_gold_dn},
        "cofire_btc_off_and_gold_up": n_off_and_up,
        "btc_off_alone": n_off_no_up,
        "gold_up_alone": n_no_off_and_up,
        "conditional_means": {
            "all":   {"n": n_all, "mean": m_all},
            "00":    {"n": n00,   "mean": m00},
            "01":    {"n": n01,   "mean": m01},
            "10":    {"n": n10,   "mean": m10},
            "11":    {"n": n11,   "mean": m11},
        },
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
