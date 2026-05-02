"""
Validate the proposed strategy fixes against PSX historical data BEFORE
implementing them.

PSX is genuinely different from generic equity markets:
  - Retail-heavy, narrow breadth, lower mean ADV/free-float
  - Currency-controlled
  - IMF/SBP cycle dominates almost everything
  - Settlement T+2

So before pushing changes that come from a "senior analyst" lens, we test
each one against PSX history. A change is only worth shipping if it has a
measurable forward-return edge OR (for risk rules) measurably reduces
drawdown without giving up too much return.

Each test prints:
  - claim     : the rule we'd implement
  - sample    : how many historical observations we got
  - baseline  : what an un-conditional bet on the same horizon would have
                returned
  - measured  : what the proposed rule would have returned
  - verdict   : PASS / FAIL / INCONCLUSIVE  (+ reason)

Output -> data/_health/strategy_fixes_validation.{md,json}
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OHLCV_DIR = ROOT / "data" / "ohlcv"
MACRO_DIR = ROOT / "data" / "macro"
FLOWS_DIR = ROOT / "data" / "flows"
OUT_DIR = ROOT / "data" / "_health"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Sector mapping for our 36-stock OHLCV universe (only what we can actually test).
SECTOR_MAP: dict[str, str] = {
    # Banks
    "FABL": "BANK", "MCB": "BANK", "MEBL": "BANK", "ABL": "BANK",
    "BAHL": "BANK", "HBL": "BANK", "NBP": "BANK", "UBL": "BANK",
    # Exploration & Production
    "OGDC": "E&P", "PPL": "E&P", "POL": "E&P", "MARI": "E&P",
    # Oil marketing
    "PSO": "OMC", "APL": "OMC", "ATRL": "OMC",
    # Power
    "HUBC": "POWER", "NPL": "POWER", "KAPCO": "POWER", "KEL": "POWER",
    # Cement
    "FCCL": "CEMENT", "MLCF": "CEMENT", "KOHC": "CEMENT", "PABC": "CEMENT",
    "DGKC": "CEMENT", "LUCK": "CEMENT",
    # Fertilizer
    "FATIMA": "FERTILIZER", "EFERT": "FERTILIZER", "FFC": "FERTILIZER",
    "ENGROH": "FERTILIZER",
    # Chemical
    "EPCL": "CHEMICAL", "LOTCHEM": "CHEMICAL", "COLG": "CHEMICAL",
    # Pharma
    "SEARL": "PHARMA",
    # Tech
    "SYS": "TECH", "TRG": "TECH",
    # Auto
    "INDU": "AUTO",
}

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_ohlcv() -> pd.DataFrame:
    """Return a wide closes DataFrame indexed by date, columns = symbols, plus volume."""
    closes: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}
    for p in sorted(OHLCV_DIR.glob("*.parquet")):
        df = pd.read_parquet(p)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.set_index("date").sort_index()
        sym = p.stem
        closes[sym] = df["close"].astype(float)
        volumes[sym] = df["volume"].astype(float)
    closes_df = pd.DataFrame(closes).sort_index()
    volumes_df = pd.DataFrame(volumes).sort_index()
    return closes_df, volumes_df


def _load_sbp_rates() -> pd.DataFrame:
    df = pd.read_parquet(MACRO_DIR / "sbp_rates.parquet")
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.set_index("date").sort_index()


def _load_policy_decisions() -> pd.DataFrame:
    raw = json.loads((MACRO_DIR / "_policy_rate_history.json").read_text())
    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    df["delta_pp"] = df["rate_pct"].diff()
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _forward_return(close: pd.Series, on_date: pd.Timestamp, horizon: int) -> float | None:
    """Return forward % return from on_date over horizon trading days."""
    if on_date not in close.index:
        # Snap to next available trading day.
        future = close.index[close.index >= on_date]
        if future.empty:
            return None
        on_date = future[0]
    pos = close.index.get_loc(on_date)
    if isinstance(pos, slice):
        pos = pos.start
    if pos + horizon >= len(close):
        return None
    p0 = close.iloc[pos]
    p1 = close.iloc[pos + horizon]
    if not p0 or not p1 or math.isnan(p0) or math.isnan(p1):
        return None
    return float((p1 - p0) / p0 * 100.0)


def _fmt(val, suffix: str = "%", placeholder: str = "n/a") -> str:
    """Format a number as `12.34%` or `n/a` if None / NaN. Safe in f-strings."""
    if val is None:
        return placeholder
    try:
        if math.isnan(val):
            return placeholder
    except TypeError:
        return placeholder
    return f"{val:.2f}{suffix}"


def _stats(returns: list[float]) -> dict:
    """Summary statistics for a list of forward returns (in %)."""
    rets = [r for r in returns if r is not None and not math.isnan(r)]
    if not rets:
        return {"n": 0, "mean": None, "median": None, "stdev": None,
                "hit_rate_up": None, "sharpe_like": None}
    n = len(rets)
    mu = mean(rets)
    md = median(rets)
    sd = pstdev(rets) if n > 1 else 0.0
    hit = sum(1 for r in rets if r > 0) / n * 100
    sharpe = (mu / sd) if sd else None
    return {
        "n": n,
        "mean": round(mu, 3),
        "median": round(md, 3),
        "stdev": round(sd, 3),
        "hit_rate_up": round(hit, 1),
        "sharpe_like": round(sharpe, 3) if sharpe is not None else None,
    }


def _baseline_returns(closes: pd.DataFrame, horizon: int, sample: int = 2000) -> list[float]:
    """Random sampling of (date, symbol) forward returns to give an
    unconditional baseline for the same horizon."""
    rng = np.random.default_rng(42)
    syms = list(closes.columns)
    rets: list[float] = []
    if not syms:
        return rets
    # Restrict to dates where at least 5 stocks trade.
    valid_dates = closes.dropna(how="all").index
    if len(valid_dates) < 50:
        return rets
    pickable = valid_dates[:-horizon - 1]
    if len(pickable) == 0:
        return rets
    for _ in range(sample):
        sym = syms[int(rng.integers(0, len(syms)))]
        d = pickable[int(rng.integers(0, len(pickable)))]
        r = _forward_return(closes[sym].dropna(), d, horizon)
        if r is not None:
            rets.append(r)
    return rets


@dataclass
class TestResult:
    test_id: str
    title: str
    claim: str
    horizon_days: int
    sample: dict = field(default_factory=dict)
    baseline: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    verdict: str = "INCONCLUSIVE"
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# TEST 1 — Rate-cut profit-taking (proposed REWRITE for nth_rate_cut_profit_taking)
# ---------------------------------------------------------------------------

def test_rate_cut_profit_taking(closes: pd.DataFrame, decisions: pd.DataFrame) -> list[TestResult]:
    """
    Existing rule: After Nth rate cut in a cycle, expect 5d MIXED profit-taking.
    End-to-end test showed 0/10 hit rate, +6.2% in the WRONG direction.

    Proposed rewrite: profit-taking only fires when:
       - rate_cuts_180d >= 5 (deep cycle)
       - kse100_5d_gte: 0.04 (over-extended bounce)

    We test each rate-cut decision and look at:
       - 5d forward universe equal-weighted return
       - 21d forward
       - We split by 'cut number in cycle' and by 'pre-decision 5d run-up'.
    """
    universe = closes.dropna(how="all")
    universe_eq = universe.pct_change().mean(axis=1) * 100  # daily % return
    universe_close = (1 + universe_eq.fillna(0) / 100).cumprod()

    cuts = decisions[decisions["delta_pp"] < 0].copy().reset_index(drop=True)

    # Identify cycles: a new cycle starts when there's a hike between cuts.
    cycle_no, n_in_cycle = [], []
    cyc, n = 0, 0
    last_was_cut = False
    for _, row in decisions.iterrows():
        if row["delta_pp"] is None or pd.isna(row["delta_pp"]):
            continue
        if row["delta_pp"] < 0:
            if not last_was_cut:
                cyc += 1
                n = 0
            n += 1
            cycle_no.append(cyc)
            n_in_cycle.append(n)
            last_was_cut = True
        elif row["delta_pp"] > 0:
            last_was_cut = False
        else:
            # Hold: if previous was cut, keep cycle alive; if hike, broken.
            pass

    cuts["cycle"] = cycle_no
    cuts["n_in_cycle"] = n_in_cycle

    rows = []
    for _, c in cuts.iterrows():
        d = c["date"]
        # Snap to nearest trading day.
        future = universe_close.index[universe_close.index >= d]
        if future.empty:
            continue
        d_snap = future[0]
        pos = universe_close.index.get_loc(d_snap)
        if pos + 21 >= len(universe_close):
            continue
        # Pre-event 5d return (proxy for "stretched bounce").
        if pos < 5:
            continue
        p_pre5 = universe_close.iloc[pos - 5]
        p0 = universe_close.iloc[pos]
        p5 = universe_close.iloc[pos + 5]
        p21 = universe_close.iloc[pos + 21]
        rows.append({
            "decision_date": d.date().isoformat(),
            "n_in_cycle": int(c["n_in_cycle"]),
            "delta_pp": float(c["delta_pp"]),
            "pre_5d_pct": float((p0 - p_pre5) / p_pre5 * 100),
            "fwd_5d_pct": float((p5 - p0) / p0 * 100),
            "fwd_21d_pct": float((p21 - p0) / p0 * 100),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return [TestResult(
            test_id="T1_rate_cut", title="Rate-cut profit-taking",
            claim="N/A", horizon_days=5, verdict="INCONCLUSIVE",
            reason="No rate-cut data within OHLCV range."
        )]

    results: list[TestResult] = []

    # Sub-test A: Old "Nth cut" rule -- does the OLD claim hold at all?
    nth = df[df["n_in_cycle"] >= 3]
    s5 = _stats(nth["fwd_5d_pct"].tolist())
    base5 = _stats(_baseline_returns(closes, 5, sample=1000))
    edge = (s5["mean"] or 0) - (base5["mean"] or 0)
    results.append(TestResult(
        test_id="T1a_old_rule", title="OLD nth_rate_cut_profit_taking (n>=3)",
        claim="After 3rd+ rate cut in cycle, universe drops over next 5d (DOWN)",
        horizon_days=5, sample=s5, baseline=base5,
        extra={"edge_vs_baseline_pct": round(edge, 3),
               "sample_table": df.to_dict("records")},
        verdict="FAIL" if (s5["n"] >= 3 and (s5["mean"] or 0) > (base5["mean"] or 0))
                else ("PASS" if s5["n"] >= 3 and (s5["mean"] or 0) < -1.0 else "INCONCLUSIVE"),
        reason=("Mean fwd-5d return is POSITIVE -- the old rule predicts the "
                "wrong direction." if (s5["mean"] or 0) > 0
                else "Direction matches but magnitude small.") if s5["n"] >= 3
                else f"Only {s5['n']} observations.",
    ))

    # Sub-test B: PROPOSED rewrite (deep cycle + over-extended bounce).
    proposed = df[(df["n_in_cycle"] >= 5) & (df["pre_5d_pct"] >= 4.0)]
    s5p = _stats(proposed["fwd_5d_pct"].tolist())
    s21p = _stats(proposed["fwd_21d_pct"].tolist())
    results.append(TestResult(
        test_id="T1b_proposed_rule", title="PROPOSED nth_rate_cut_profit_taking",
        claim="After 5th+ rate cut AND universe up >=4% in 5d, expect 5d pullback (DOWN)",
        horizon_days=5, sample=s5p, baseline=base5,
        extra={"sample_dates": proposed["decision_date"].tolist(),
               "fwd_21d": s21p},
        verdict="INCONCLUSIVE" if s5p["n"] < 3
                else ("PASS" if (s5p["mean"] or 0) < -1.0 else "FAIL"),
        reason=(f"Only {s5p['n']} observations satisfy both conditions in our "
                f"history -- can't confirm. Need more cycle history."
                if s5p["n"] < 3
                else (f"Avg fwd 5d = {_fmt(s5p['mean'])} -- predicts pullback."
                      if (s5p["mean"] or 0) < -1.0
                      else f"Avg fwd 5d = {_fmt(s5p['mean'])} -- not a pullback "
                           f"on average; rule still doesn't earn its keep.")),
    ))

    # Sub-test C: What ACTUALLY predicts post-rate-cut moves?
    # Try: just "did the universe rally hard pre-cut?" → does it pull back?
    rallied = df[df["pre_5d_pct"] >= 3.0]
    s5r = _stats(rallied["fwd_5d_pct"].tolist())
    results.append(TestResult(
        test_id="T1c_diagnostic", title="DIAGNOSTIC: pre-cut 5d run-up >=3%",
        claim="(diagnostic) When universe is up >=3% in 5d before any rate cut, "
              "does it pull back over next 5d?",
        horizon_days=5, sample=s5r, baseline=base5,
        extra={"sample_dates": rallied["decision_date"].tolist()},
        verdict="INCONCLUSIVE" if s5r["n"] < 3
                else ("PASS-DIAGNOSTIC" if (s5r["mean"] or 0) < (base5["mean"] or 0)
                      else "FAIL-DIAGNOSTIC"),
        reason=(f"After pre-cut 3%+ rallies (n={s5r['n']}), avg fwd 5d = "
                f"{_fmt(s5r['mean'])} vs baseline {_fmt(base5['mean'])}."
                if s5r["n"] >= 3 else f"Only {s5r['n']} samples."),
    ))

    return results


# ---------------------------------------------------------------------------
# TEST 2 — MF freshness decay (proposed REPLACE 60d hard veto with weight decay)
# ---------------------------------------------------------------------------

def test_mf_freshness_decay(closes: pd.DataFrame) -> list[TestResult]:
    """
    Existing: MF triggers veto silently if data > 60 days old.
    Proposed: Replace with weight decay (full weight at 30d, half at 60d, zero at 90d).

    Test idea: For every published MF report month, identify the top-10
    accumulated stocks. Compute their forward equal-weighted return at
    +30d, +60d, +75d, +90d. If alpha persists past day 60, the hard veto
    is throwing away signal. If alpha has decayed by day 60, the veto is right.

    LIMITATION: We only have 2 monthly reports right now (June-2025 and
    Jan-2026), so this is INCONCLUSIVE on real history. We honestly report
    that and recommend collecting more data before changing the rule.
    """
    fp = FLOWS_DIR / "mf_top_holdings_summary.parquet"
    if not fp.exists():
        return [TestResult(
            test_id="T2_mf_decay", title="MF freshness decay",
            claim="N/A", horizon_days=60, verdict="INCONCLUSIVE",
            reason="No MF summary parquet found.")]

    mf = pd.read_parquet(fp)
    mf["as_of_month"] = pd.to_datetime(mf["as_of_month"]).dt.tz_localize(None)
    mf["report_pub_date"] = pd.to_datetime(mf["report_pub_date"]).dt.tz_localize(None)
    months = sorted(mf["as_of_month"].dropna().unique())

    results: list[TestResult] = []
    horizons = [30, 60, 75, 90]
    decay_table: dict[int, list[float]] = {h: [] for h in horizons}

    for m in months:
        snap = mf[mf["as_of_month"] == m].copy()
        if "change_mom_pct_pts" not in snap.columns:
            continue
        snap = snap.sort_values("change_mom_pct_pts", ascending=False).head(10)
        accum_syms = [s for s in snap["symbol"].dropna().unique() if s in closes.columns]
        if not accum_syms:
            continue
        pub = snap["report_pub_date"].dropna().min()
        if pd.isna(pub):
            continue
        for h in horizons:
            rets = []
            for sym in accum_syms:
                r = _forward_return(closes[sym].dropna(), pub, h)
                if r is not None:
                    rets.append(r)
            if rets:
                decay_table[h].append(mean(rets))

    base30 = _stats(_baseline_returns(closes, 30, sample=600))
    base60 = _stats(_baseline_returns(closes, 60, sample=600))
    base90 = _stats(_baseline_returns(closes, 90, sample=600))

    decay_summary = {
        h: ({"mean_top10_basket": round(mean(v), 3) if v else None,
             "n_reports": len(v)})
        for h, v in decay_table.items()
    }

    n_obs = len(months)
    if n_obs < 4:
        verdict = "INCONCLUSIVE"
        reason = (f"Only {n_obs} MF report months available. We CANNOT "
                  f"empirically place the alpha-decay curve yet. "
                  f"Recommend: keep the 60d hard veto until we have >= 12 "
                  f"reports, then revisit. (Risk of weight-decay change with "
                  f"so little data is HIGH.)")
        results.append(TestResult(
            test_id="T2_mf_decay", title="MF freshness decay",
            claim="Replace 60d hard veto with weight decay 30d=1.0, 60d=0.5, 90d=0",
            horizon_days=60,
            sample={"n_months": n_obs}, baseline=base60,
            extra={"decay_summary": decay_summary,
                   "baseline_30d": base30, "baseline_60d": base60,
                   "baseline_90d": base90},
            verdict=verdict, reason=reason))
        return results

    # If we ever have enough data, evaluate properly:
    persists_60 = (decay_summary[60]["mean_top10_basket"] or 0) > (base60["mean"] or 0)
    persists_90 = (decay_summary[90]["mean_top10_basket"] or 0) > (base90["mean"] or 0)
    verdict = "PASS" if persists_60 else "FAIL"
    reason = ("Alpha persists past 60d, so weight decay is correct."
              if persists_60 else "Alpha gone by 60d, hard veto is right.")
    results.append(TestResult(
        test_id="T2_mf_decay", title="MF freshness decay",
        claim="Replace 60d hard veto with weight decay 30d=1.0, 60d=0.5, 90d=0",
        horizon_days=60,
        sample={"n_months": n_obs}, baseline=base60,
        extra={"decay_summary": decay_summary,
               "persists_60d": persists_60, "persists_90d": persists_90,
               "baseline_30d": base30, "baseline_60d": base60,
               "baseline_90d": base90},
        verdict=verdict, reason=reason))
    return results


# ---------------------------------------------------------------------------
# TEST 3 — Banking NIM proxy
# ---------------------------------------------------------------------------

def test_banking_nim(closes: pd.DataFrame, sbp: pd.DataFrame) -> list[TestResult]:
    """
    Proposed: New trigger banking_nim_widening / banking_nim_compressing.

    DATA REALITY CHECK: Our sbp_rates.parquet only has policy_rate_pct,
    kibor_3m_pct and tbill_3m_pct populated (the 6M/12M/PIB columns are
    null). So we use two proxies that work with what we have:

    Proxy A (spread): T-bill 3M − KIBOR 3M.
        Banks hold T-bills as assets, fund themselves at KIBOR-ish.
        A widening spread is a (small but directionally correct)
        proxy for NIM tailwind.

    Proxy B (level): Policy rate top quartile.
        On PSX, when policy rate is in the top quartile of its 5-year
        range, banks structurally earn more on their large T-bill book
        against largely-fixed CASA cost. A simpler & cleaner test.
    """
    bank_syms = [s for s in ("FABL", "MCB", "MEBL", "ABL", "BAHL",
                             "HBL", "NBP", "UBL") if s in closes.columns]
    if not bank_syms:
        return [TestResult(
            test_id="T3_nim", title="Banking NIM",
            claim="N/A", horizon_days=21, verdict="INCONCLUSIVE",
            reason="No bank tickers in OHLCV.")]
    bank_close = closes[bank_syms].mean(axis=1).dropna()
    base = _stats(_baseline_returns(closes[bank_syms], 21, sample=800))
    base90 = _stats(_baseline_returns(closes[bank_syms], 90, sample=800))

    results: list[TestResult] = []

    # ---------- Proxy A: T-bill 3M − KIBOR 3M spread ----------
    spread = (sbp["tbill_3m_pct"] - sbp["kibor_3m_pct"]).dropna()
    if len(spread) > 30:
        spread_chg = spread - spread.shift(60)
        # Use a much smaller threshold (15bps over 60d) given the spread
        # is structurally narrow (both rates anchored to policy).
        widening_dates = spread_chg[spread_chg >= 0.15].index
        compressing_dates = spread_chg[spread_chg <= -0.15].index

        def _monthly_sample(dates) -> list[pd.Timestamp]:
            if len(dates) == 0:
                return []
            df = pd.DataFrame({"d": dates})
            df["m"] = df["d"].dt.to_period("M")
            return df.groupby("m")["d"].first().tolist()

        wide_obs = _monthly_sample(widening_dates)
        comp_obs = _monthly_sample(compressing_dates)

        def _basket_fwd(dates, h=21):
            out = []
            for d in dates:
                r = _forward_return(bank_close, d, h)
                if r is not None:
                    out.append(r)
            return out

        s_wide = _stats(_basket_fwd(wide_obs))
        s_comp = _stats(_basket_fwd(comp_obs))
        edge_w = (s_wide["mean"] or 0) - (base["mean"] or 0)
        edge_diff = (s_wide["mean"] or 0) - (s_comp["mean"] or 0)

        results.append(TestResult(
            test_id="T3a_nim_spread", title="Banking NIM via T-bill / KIBOR spread",
            claim="When (T-bill 3M − KIBOR 3M) widens >=15bps over 60d, "
                  "bank basket beats baseline over next 21d.",
            horizon_days=21, sample=s_wide, baseline=base,
            extra={"compressing_stats": s_comp,
                   "edge_vs_baseline_pp": round(edge_w, 3),
                   "edge_vs_compressing_pp": round(edge_diff, 3),
                   "n_wide_months": len(wide_obs),
                   "n_comp_months": len(comp_obs)},
            verdict=("INCONCLUSIVE" if s_wide["n"] < 6
                     else ("PASS" if edge_w > 1.0 and edge_diff > 2.0
                           else ("WEAK-EFFECT" if abs(edge_diff) < 1.0 else "FAIL"))),
            reason=(f"Widening months (n={s_wide['n']}) avg={_fmt(s_wide['mean'])} "
                    f"vs compressing (n={s_comp['n']}) avg={_fmt(s_comp['mean'])} "
                    f"vs baseline {_fmt(base['mean'])}. "
                    f"Edge widening-vs-baseline: {_fmt(edge_w, suffix='pp')}. "
                    f"Edge widening-vs-compressing: {_fmt(edge_diff, suffix='pp')}."),
        ))

    # ---------- Proxy B: policy rate top quartile (90d horizon) ----------
    pol = sbp["policy_rate_pct"].dropna()
    if len(pol) > 100:
        q75 = pol.quantile(0.75)
        q25 = pol.quantile(0.25)
        # Sample monthly to keep observations roughly independent.
        monthly = pol.resample("ME").last().dropna()
        high_months = monthly[monthly >= q75].index
        low_months = monthly[monthly <= q25].index

        def _basket_fwd(dates, h=90):
            out = []
            for d in dates:
                r = _forward_return(bank_close, d, h)
                if r is not None:
                    out.append(r)
            return out

        s_high = _stats(_basket_fwd(high_months, 90))
        s_low = _stats(_basket_fwd(low_months, 90))
        edge = (s_high["mean"] or 0) - (s_low["mean"] or 0)

        results.append(TestResult(
            test_id="T3b_nim_rate_level", title="Banking NIM via policy-rate level",
            claim=f"When policy rate is in its top quartile (>={q75:.2f}%), "
                  f"bank basket outperforms when it is in its bottom quartile "
                  f"(<={q25:.2f}%) over next 90d.",
            horizon_days=90, sample=s_high, baseline=base90,
            extra={"low_rate_stats": s_low,
                   "edge_high_vs_low_pp": round(edge, 3),
                   "n_high_months": len(high_months),
                   "n_low_months": len(low_months),
                   "q75_pct": round(q75, 2), "q25_pct": round(q25, 2)},
            verdict=("INCONCLUSIVE" if s_high["n"] < 6 or s_low["n"] < 6
                     else ("PASS" if edge > 3.0 else
                           ("WEAK-EFFECT" if abs(edge) < 2.0 else
                            ("FAIL-INVERTED" if edge < -3.0 else "FAIL")))),
            reason=(f"High-rate months (n={s_high['n']}) fwd 90d = "
                    f"{_fmt(s_high['mean'])}. Low-rate months "
                    f"(n={s_low['n']}) fwd 90d = {_fmt(s_low['mean'])}. "
                    f"Edge = {_fmt(edge, suffix='pp')}."),
        ))

    if not results:
        results.append(TestResult(
            test_id="T3_nim", title="Banking NIM",
            claim="N/A", horizon_days=21, verdict="INCONCLUSIVE",
            reason="Insufficient SBP rate data for either proxy."))
    return results


# ---------------------------------------------------------------------------
# TEST 4 — PSX volume regime
# ---------------------------------------------------------------------------

def test_volume_regime(closes: pd.DataFrame, volumes: pd.DataFrame) -> list[TestResult]:
    """
    Generic analyst belief: "Volume confirms direction" → high-volume up days
    are followed by more upside; low-volume up days fade.

    PSX-specific concern: PSX is retail-driven and turnover spikes often
    correspond to FOMO tops. So this generic belief might INVERT on PSX.

    Test: For each (date, symbol) where the daily return > +1.5%, classify
    by today's volume vs 20-day median. Compute forward 5d return. Compare
    "high-volume up" vs "low-volume up".
    """
    rets = closes.pct_change() * 100
    vol_med20 = volumes.rolling(20, min_periods=10).median()
    vol_ratio = volumes / vol_med20

    high_up: list[float] = []
    low_up: list[float] = []
    for sym in closes.columns:
        sym_rets = rets[sym].dropna()
        sym_close = closes[sym].dropna()
        sym_ratio = vol_ratio[sym].dropna()
        for d, r in sym_rets.items():
            if r < 1.5 or d not in sym_ratio.index:
                continue
            ratio = sym_ratio.loc[d]
            if pd.isna(ratio):
                continue
            fwd = _forward_return(sym_close, d, 5)
            if fwd is None:
                continue
            if ratio >= 1.5:
                high_up.append(fwd)
            elif ratio <= 0.7:
                low_up.append(fwd)

    s_high = _stats(high_up)
    s_low = _stats(low_up)
    base = _stats(_baseline_returns(closes, 5, sample=1500))
    edge = (s_high["mean"] or 0) - (s_low["mean"] or 0)

    # PASS if high-vol-up beats low-vol-up by >0.5pp on fwd 5d.
    return [TestResult(
        test_id="T4_volume_regime", title="PSX volume confirms direction",
        claim="On PSX, +1.5% days on >=1.5x median volume outperform "
              "+1.5% days on <=0.7x median volume over next 5d",
        horizon_days=5, sample=s_high, baseline=base,
        extra={"low_volume_up_stats": s_low,
               "edge_high_minus_low_pp": round(edge, 3)},
        verdict=("INCONCLUSIVE" if s_high["n"] < 30 or s_low["n"] < 30
                 else ("PASS" if edge > 0.5 else
                       ("INVERTED" if edge < -0.5 else "WEAK-EFFECT"))),
        reason=(f"High-vol up: mean fwd5d = {_fmt(s_high['mean'])} "
                f"(n={s_high['n']}). Low-vol up: {_fmt(s_low['mean'])} "
                f"(n={s_low['n']}). Edge = {_fmt(edge, suffix='pp')}."),
    )]


# ---------------------------------------------------------------------------
# TEST 5 — Falling knives (Rule 10 in proposal)
# ---------------------------------------------------------------------------

def test_falling_knives(closes: pd.DataFrame) -> list[TestResult]:
    """
    Rule 10 proposal: "Don't catch falling knives" -- avoid issuing BUY when
    21d return is < -10%.

    PSX-specific concern: After SBP cycle pivots and IMF approvals, deeply
    sold-off names often rip back. So the "no falling knives" rule could
    actually HURT us on PSX.

    Test: Find every (date, sym) where 21d return < -10%. Compute
    forward 21d return. If forward is positive on average vs baseline,
    "don't buy" is WRONG for PSX -- knives bounce here.
    """
    closes_clean = closes.dropna(how="all")
    rets_21d_back = closes_clean.pct_change(21) * 100

    knife_returns: list[float] = []
    deep_knife_returns: list[float] = []  # < -20%
    base_returns: list[float] = []

    for sym in closes_clean.columns:
        sym_close = closes_clean[sym].dropna()
        sym_back = sym_close.pct_change(21) * 100
        for d in sym_back.index:
            r_back = sym_back.loc[d]
            if pd.isna(r_back):
                continue
            fwd = _forward_return(sym_close, d, 21)
            if fwd is None:
                continue
            if r_back <= -10.0:
                knife_returns.append(fwd)
            if r_back <= -20.0:
                deep_knife_returns.append(fwd)
            base_returns.append(fwd)

    s_knife = _stats(knife_returns)
    s_deep = _stats(deep_knife_returns)
    s_base = _stats(base_returns)
    edge_knife = (s_knife["mean"] or 0) - (s_base["mean"] or 0)
    edge_deep = (s_deep["mean"] or 0) - (s_base["mean"] or 0)

    # Sharpe-aware: even if mean is positive, a wide stdev at the same
    # level as baseline ≠ improvement.
    sharpe_knife = s_knife.get("sharpe_like") or 0
    sharpe_base = s_base.get("sharpe_like") or 0

    return [TestResult(
        test_id="T5_falling_knives", title="Falling knives on PSX",
        claim="(Rule 10) Avoid BUY when 21d return is <= -10% (don't catch "
              "falling knives). Tested as: do knives bounce on PSX?",
        horizon_days=21, sample=s_knife, baseline=s_base,
        extra={"deep_knife_<=_-20pct_stats": s_deep,
               "edge_knife_pp": round(edge_knife, 3),
               "edge_deep_knife_pp": round(edge_deep, 3),
               "sharpe_knife_minus_base": round(sharpe_knife - sharpe_base, 3)},
        verdict=("PASS" if edge_knife < -1.0
                 else ("FAIL-INVERTED" if edge_knife > 1.0
                       else "WEAK-EFFECT")),
        reason=(f"21d knives: avg fwd 21d = {_fmt(s_knife['mean'])} "
                f"(n={s_knife['n']}) vs baseline {_fmt(s_base['mean'])} "
                f"(n={s_base['n']}). Deep knives (-20%+): "
                f"{_fmt(s_deep['mean'])} (n={s_deep['n']}). "
                f"On PSX, the verdict is: "
                f"{'avoid knives' if edge_knife < -1.0 else 'knives BOUNCE -- generic rule HURTS us'}"),
    )]


# ---------------------------------------------------------------------------
# TEST 6 — Hot sector entries (Rule 11 in proposal)
# ---------------------------------------------------------------------------

def test_hot_sector_entries(closes: pd.DataFrame) -> list[TestResult]:
    """
    Rule 11 proposal: "Sector concentration in your active list" -- limit how
    much your active list leans into one sector. The ASSUMPTION underneath is
    that the hottest sector mean-reverts.

    Test: Each Monday compute 21d sector returns (eq-weighted within sector).
    Top sector this week → buy a stock in it → forward 21d return.
    Compare to bottom sector → buy a stock → forward 21d return.
    If "buy hot sector" actually works on PSX, the rule is misguided.
    """
    sector_to_syms: dict[str, list[str]] = {}
    for sym in closes.columns:
        sec = SECTOR_MAP.get(sym)
        if sec:
            sector_to_syms.setdefault(sec, []).append(sym)

    if len(sector_to_syms) < 3:
        return [TestResult(
            test_id="T6_hot_sector", title="Hot-sector entries on PSX",
            claim="N/A", horizon_days=21, verdict="INCONCLUSIVE",
            reason="Not enough sectors mapped.")]

    # Build sector daily eq-weighted returns.
    daily = closes.pct_change()
    sector_daily = pd.DataFrame({
        sec: daily[syms].mean(axis=1)
        for sec, syms in sector_to_syms.items()
    })
    # 21d trailing return, sampled weekly (Mondays).
    trailing_21 = (1 + sector_daily).rolling(21).apply(np.prod, raw=True) - 1
    trailing_21 = trailing_21.dropna(how="all")
    weekly_dates = trailing_21.index[::5]

    hot_returns: list[float] = []
    cold_returns: list[float] = []
    for d in weekly_dates:
        snap = trailing_21.loc[d].dropna()
        if len(snap) < 3:
            continue
        hot_sector = snap.idxmax()
        cold_sector = snap.idxmin()
        for sym in sector_to_syms[hot_sector]:
            r = _forward_return(closes[sym].dropna(), d, 21)
            if r is not None:
                hot_returns.append(r)
        for sym in sector_to_syms[cold_sector]:
            r = _forward_return(closes[sym].dropna(), d, 21)
            if r is not None:
                cold_returns.append(r)

    s_hot = _stats(hot_returns)
    s_cold = _stats(cold_returns)
    edge = (s_hot["mean"] or 0) - (s_cold["mean"] or 0)

    return [TestResult(
        test_id="T6_hot_sector", title="Hot-sector entries on PSX",
        claim="(Rule 11 underlying assumption) Hot sectors mean-revert on "
              "PSX, so leaning into the hottest sector underperforms.",
        horizon_days=21, sample=s_hot, baseline=s_cold,
        extra={"cold_sector_stats": s_cold,
               "edge_hot_minus_cold_pp": round(edge, 3)},
        verdict=("PASS" if edge < -1.0
                 else ("FAIL-INVERTED" if edge > 1.0
                       else "WEAK-EFFECT")),
        reason=(f"Hot-sector buys: avg fwd 21d = {_fmt(s_hot['mean'])} "
                f"(n={s_hot['n']}). Cold-sector buys: {_fmt(s_cold['mean'])} "
                f"(n={s_cold['n']}). Edge = {_fmt(edge, suffix='pp')}. "
                f"{'Hot sectors revert -- rule is sound.' if edge < -1.0 else ('Hot sectors keep trending on PSX -- the cap may LIMIT alpha.' if edge > 1.0 else 'No clear effect.')}"),
    )]


# ---------------------------------------------------------------------------
# TEST 7 — Bi-weekly rebalance with 4% drift trigger
# ---------------------------------------------------------------------------

def test_rebalance_frequency(closes: pd.DataFrame) -> list[TestResult]:
    """
    Proposed: bi-weekly Phase-1 rebalance with a 4% per-name drift trigger
    instead of pure monthly.

    Test as a stylized backtest. We use a rolling momentum (63d) score as a
    PROXY for Phase-1's rank (since Phase-1 ranking depends on a lot of live
    inputs we can't reconstruct). Each rebalance: long top 5 of the 17-stock
    universe, equal-weight, 100bps round-trip cost.

    Compare:
      - monthly rebalance
      - bi-weekly rebalance
      - bi-weekly rebalance with 4% drift trigger

    Compare total return, Sharpe, n_trades.
    """
    daily = closes.pct_change().fillna(0)
    score = closes.pct_change(63)

    def _backtest(rebal_dates, drift_trigger=None):
        weights = pd.Series(0.0, index=closes.columns)
        port = pd.Series(0.0, index=closes.index)
        prev_value = 1.0
        port.iloc[0] = 1.0
        n_trades = 0
        last_rebal_idx = 0
        for i, d in enumerate(closes.index):
            if i == 0:
                continue
            ret = (daily.loc[d] * weights).sum()
            new_val = prev_value * (1 + ret)
            do_rebal = (d in rebal_dates)
            if drift_trigger and weights.sum() > 0:
                # Live drift = sum of |actual_w - target_w| / 2  ≈ w drift
                # Target = equal weight among holdings (top-5 from last rebal).
                held = weights[weights > 0].index
                if len(held) > 0:
                    eq_target = 1.0 / len(held)
                    drift = (weights[held] - eq_target).abs().max()
                    if drift > drift_trigger:
                        do_rebal = True
            if do_rebal:
                snap = score.loc[d].dropna()
                if len(snap) >= 5:
                    top5 = snap.nlargest(5).index
                    new_w = pd.Series(0.0, index=closes.columns)
                    new_w[top5] = 1.0 / 5.0
                    turnover = (new_w - weights).abs().sum() / 2.0
                    new_val = new_val * (1 - 0.01 * turnover)
                    weights = new_w
                    n_trades += int(turnover * 5)
            prev_value = new_val
            port.iloc[i] = new_val
            # Drift weights with returns until next rebal:
            if (daily.loc[d] * weights).sum() != 0:
                weights = weights * (1 + daily.loc[d]) / (1 + ret) if (1 + ret) else weights
        return port, n_trades

    # Build rebal date grids.
    monthly = closes.groupby(closes.index.to_period("M")).head(1).index
    biweekly = closes.iloc[::10].index  # ~ every 2 weeks

    pf_monthly, t_m = _backtest(set(monthly))
    pf_biweekly, t_b = _backtest(set(biweekly))
    pf_biweekly_drift, t_bd = _backtest(set(biweekly), drift_trigger=0.04)

    def _summary(pf):
        rets = pf.pct_change().dropna()
        if rets.empty:
            return {"total_return_pct": 0, "ann_sharpe": None, "max_dd_pct": 0}
        total = (pf.iloc[-1] / pf.iloc[0] - 1) * 100
        ann = rets.mean() * 252
        vol = rets.std() * (252 ** 0.5)
        sharpe = ann / vol if vol else None
        cum = (1 + rets).cumprod()
        dd = (cum / cum.cummax() - 1).min() * 100
        return {"total_return_pct": round(total, 2),
                "ann_sharpe": round(sharpe, 3) if sharpe else None,
                "max_dd_pct": round(dd, 2)}

    sm, sb, sbd = _summary(pf_monthly), _summary(pf_biweekly), _summary(pf_biweekly_drift)

    # Verdict: PASS if biweekly+drift Sharpe is >= monthly Sharpe AND total return is
    # within 95% of monthly's (we accept slightly lower return for higher Sharpe / lower DD)
    sharpe_m = sm.get("ann_sharpe") or 0
    sharpe_bd = sbd.get("ann_sharpe") or 0
    ret_m = sm.get("total_return_pct") or 0
    ret_bd = sbd.get("total_return_pct") or 0

    verdict = "INCONCLUSIVE"
    reason = ""
    if sharpe_bd >= sharpe_m * 1.05 and ret_bd >= ret_m * 0.95:
        verdict = "PASS"
        reason = ("Bi-weekly+drift improves Sharpe by "
                  f"{(sharpe_bd-sharpe_m):+.2f} with {t_bd} fills vs {t_m} -- worth it.")
    elif sharpe_bd < sharpe_m * 0.9 or ret_bd < ret_m * 0.85:
        verdict = "FAIL"
        reason = ("Bi-weekly+drift adds turnover cost without proportional "
                  f"Sharpe lift (Δsharpe={sharpe_bd-sharpe_m:+.2f}, "
                  f"trade count {t_bd} vs monthly {t_m}).")
    else:
        verdict = "WEAK-EFFECT"
        reason = ("Marginal differences -- bi-weekly+drift roughly matches "
                  "monthly. Monthly is operationally simpler; keep it.")

    return [TestResult(
        test_id="T7_rebalance", title="Bi-weekly w/ 4% drift trigger",
        claim="Bi-weekly Phase-1 with 4% per-name drift trigger improves "
              "Sharpe / drawdown vs monthly rebalance after 100bps "
              "round-trip cost.",
        horizon_days=0, sample={"backtest_horizon_days": int((closes.index[-1] - closes.index[0]).days)},
        baseline={},
        extra={
            "monthly": {**sm, "n_fills": t_m},
            "biweekly_no_drift": {**sb, "n_fills": t_b},
            "biweekly_with_4pct_drift": {**sbd, "n_fills": t_bd},
        },
        verdict=verdict, reason=reason,
    )]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_report(results: list[TestResult]) -> None:
    json_path = OUT_DIR / "strategy_fixes_validation.json"
    md_path = OUT_DIR / "strategy_fixes_validation.md"

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "tests": [r.to_dict() for r in results],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    md = ["# Strategy fixes — validation against PSX history",
          "",
          f"_Generated: {payload['generated_at']}_",
          "",
          "Each proposed change has been tested against the actual PSX",
          "historical data we have on disk. PSX is unique; generic analyst",
          "rules don't always work. Verdict is one of:",
          "",
          "- **PASS** — the change has measurable PSX-historical edge.",
          "- **FAIL** — the change does NOT earn its keep on PSX.",
          "- **FAIL-INVERTED** — the rule predicts the WRONG direction; PSX behaves opposite.",
          "- **INVERTED** — directionally opposite to the generic-analyst expectation; consider flipping the rule.",
          "- **WEAK-EFFECT** — small effect, not worth the complexity.",
          "- **INCONCLUSIVE** — sample too small to call; collect more data first.",
          "",
          "---",
          ""]
    for r in results:
        md.append(f"## {r.title}  —  **{r.verdict}**")
        md.append(f"_Test ID: `{r.test_id}` · horizon: {r.horizon_days} day(s)_")
        md.append("")
        md.append(f"**Claim being tested:** {r.claim}")
        md.append("")
        md.append(f"**Sample:** `{r.sample}`")
        if r.baseline:
            md.append("")
            md.append(f"**Baseline (random PSX picks, same horizon):** `{r.baseline}`")
        if r.extra:
            md.append("")
            md.append("**Detail:**")
            md.append("```json")
            md.append(json.dumps(r.extra, indent=2, default=str))
            md.append("```")
        md.append("")
        md.append(f"**Reasoning:** {r.reason}")
        md.append("")
        md.append("---")
        md.append("")

    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")


def main() -> None:
    print("Loading PSX history...")
    closes, volumes = _load_ohlcv()
    sbp = _load_sbp_rates()
    decisions = _load_policy_decisions()
    print(f"  closes: {closes.shape}, volumes: {volumes.shape}, "
          f"sbp: {sbp.shape}, decisions: {len(decisions)}")

    results: list[TestResult] = []

    print("\nTEST 1: Rate-cut profit-taking ...")
    results.extend(test_rate_cut_profit_taking(closes, decisions))

    print("TEST 2: MF freshness decay ...")
    results.extend(test_mf_freshness_decay(closes))

    print("TEST 3: Banking NIM widening ...")
    results.extend(test_banking_nim(closes, sbp))

    print("TEST 4: PSX volume regime ...")
    results.extend(test_volume_regime(closes, volumes))

    print("TEST 5: Falling knives on PSX ...")
    results.extend(test_falling_knives(closes))

    print("TEST 6: Hot sector entries on PSX ...")
    results.extend(test_hot_sector_entries(closes))

    print("TEST 7: Bi-weekly w/ drift rebalance ...")
    results.extend(test_rebalance_frequency(closes))

    print("\n--- VERDICTS ---")
    for r in results:
        print(f"  [{r.verdict:18}] {r.title}")
        print(f"                       {r.reason}")

    write_report(results)


if __name__ == "__main__":
    main()
