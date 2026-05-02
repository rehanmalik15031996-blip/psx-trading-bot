# Strategy fixes — validation against PSX history

_Generated: 2026-05-02T20:05:16.266621+00:00_

Each proposed change has been tested against the actual PSX
historical data we have on disk. PSX is unique; generic analyst
rules don't always work. Verdict is one of:

- **PASS** — the change has measurable PSX-historical edge.
- **FAIL** — the change does NOT earn its keep on PSX.
- **FAIL-INVERTED** — the rule predicts the WRONG direction; PSX behaves opposite.
- **INVERTED** — directionally opposite to the generic-analyst expectation; consider flipping the rule.
- **WEAK-EFFECT** — small effect, not worth the complexity.
- **INCONCLUSIVE** — sample too small to call; collect more data first.

---

## OLD nth_rate_cut_profit_taking (n>=3)  —  **INCONCLUSIVE**
_Test ID: `T1a_old_rule` · horizon: 5 day(s)_

**Claim being tested:** After 3rd+ rate cut in cycle, universe drops over next 5d (DOWN)

**Sample:** `{'n': 5, 'mean': 0.266, 'median': -0.265, 'stdev': 1.479, 'hit_rate_up': 40.0, 'sharpe_like': 0.18}`

**Baseline (random PSX picks, same horizon):** `{'n': 998, 'mean': 0.369, 'median': 0.192, 'stdev': 6.693, 'hit_rate_up': 51.6, 'sharpe_like': 0.055}`

**Detail:**
```json
{
  "edge_vs_baseline_pct": -0.103,
  "sample_table": [
    {
      "decision_date": "2024-06-10",
      "n_in_cycle": 1,
      "delta_pp": -1.5,
      "pre_5d_pct": -2.976017679233054,
      "fwd_5d_pct": 6.533558048410034,
      "fwd_21d_pct": 8.761837496375486
    },
    {
      "decision_date": "2024-07-29",
      "n_in_cycle": 2,
      "delta_pp": -1.0,
      "pre_5d_pct": 0.6717803268141463,
      "fwd_5d_pct": -3.0278578208772062,
      "fwd_21d_pct": -1.6295631567470967
    },
    {
      "decision_date": "2024-09-12",
      "n_in_cycle": 3,
      "delta_pp": -2.0,
      "pre_5d_pct": 0.2153172072081062,
      "fwd_5d_pct": -0.7076839958323367,
      "fwd_21d_pct": 1.6288059435986946
    },
    {
      "decision_date": "2024-11-04",
      "n_in_cycle": 4,
      "delta_pp": -2.5,
      "pre_5d_pct": 2.0332879878730292,
      "fwd_5d_pct": 1.7271861960641348,
      "fwd_21d_pct": 13.868995248657123
    },
    {
      "decision_date": "2024-12-16",
      "n_in_cycle": 5,
      "delta_pp": -2.0,
      "pre_5d_pct": 4.3634420274725745,
      "fwd_5d_pct": -1.657267840228222,
      "fwd_21d_pct": -3.1952954871402444
    },
    {
      "decision_date": "2025-01-27",
      "n_in_cycle": 6,
      "delta_pp": -1.0,
      "pre_5d_pct": -1.98147822596091,
      "fwd_5d_pct": -0.2645327713602338,
      "fwd_21d_pct": 1.5040788066549542
    },
    {
      "decision_date": "2025-05-05",
      "n_in_cycle": 7,
      "delta_pp": -1.0,
      "pre_5d_pct": -1.0588339319310995,
      "fwd_5d_pct": 2.234463783122905,
      "fwd_21d_pct": 8.176545650092642
    }
  ]
}
```

**Reasoning:** Mean fwd-5d return is POSITIVE -- the old rule predicts the wrong direction.

---

## PROPOSED nth_rate_cut_profit_taking  —  **INCONCLUSIVE**
_Test ID: `T1b_proposed_rule` · horizon: 5 day(s)_

**Claim being tested:** After 5th+ rate cut AND universe up >=4% in 5d, expect 5d pullback (DOWN)

**Sample:** `{'n': 1, 'mean': -1.657, 'median': -1.657, 'stdev': 0.0, 'hit_rate_up': 0.0, 'sharpe_like': None}`

**Baseline (random PSX picks, same horizon):** `{'n': 998, 'mean': 0.369, 'median': 0.192, 'stdev': 6.693, 'hit_rate_up': 51.6, 'sharpe_like': 0.055}`

**Detail:**
```json
{
  "sample_dates": [
    "2024-12-16"
  ],
  "fwd_21d": {
    "n": 1,
    "mean": -3.195,
    "median": -3.195,
    "stdev": 0.0,
    "hit_rate_up": 0.0,
    "sharpe_like": null
  }
}
```

**Reasoning:** Only 1 observations satisfy both conditions in our history -- can't confirm. Need more cycle history.

---

## DIAGNOSTIC: pre-cut 5d run-up >=3%  —  **INCONCLUSIVE**
_Test ID: `T1c_diagnostic` · horizon: 5 day(s)_

**Claim being tested:** (diagnostic) When universe is up >=3% in 5d before any rate cut, does it pull back over next 5d?

**Sample:** `{'n': 1, 'mean': -1.657, 'median': -1.657, 'stdev': 0.0, 'hit_rate_up': 0.0, 'sharpe_like': None}`

**Baseline (random PSX picks, same horizon):** `{'n': 998, 'mean': 0.369, 'median': 0.192, 'stdev': 6.693, 'hit_rate_up': 51.6, 'sharpe_like': 0.055}`

**Detail:**
```json
{
  "sample_dates": [
    "2024-12-16"
  ]
}
```

**Reasoning:** Only 1 samples.

---

## MF freshness decay  —  **INCONCLUSIVE**
_Test ID: `T2_mf_decay` · horizon: 60 day(s)_

**Claim being tested:** Replace 60d hard veto with weight decay 30d=1.0, 60d=0.5, 90d=0

**Sample:** `{'n_months': 2}`

**Baseline (random PSX picks, same horizon):** `{'n': 600, 'mean': 4.338, 'median': 1.454, 'stdev': 20.671, 'hit_rate_up': 53.8, 'sharpe_like': 0.21}`

**Detail:**
```json
{
  "decay_summary": {
    "30": {
      "mean_top10_basket": -2.973,
      "n_reports": 2
    },
    "60": {
      "mean_top10_basket": 17.564,
      "n_reports": 1
    },
    "75": {
      "mean_top10_basket": 16.205,
      "n_reports": 1
    },
    "90": {
      "mean_top10_basket": 18.016,
      "n_reports": 1
    }
  },
  "baseline_30d": {
    "n": 600,
    "mean": 2.231,
    "median": 0.699,
    "stdev": 13.341,
    "hit_rate_up": 52.8,
    "sharpe_like": 0.167
  },
  "baseline_60d": {
    "n": 600,
    "mean": 4.338,
    "median": 1.454,
    "stdev": 20.671,
    "hit_rate_up": 53.8,
    "sharpe_like": 0.21
  },
  "baseline_90d": {
    "n": 600,
    "mean": 6.708,
    "median": 3.938,
    "stdev": 25.364,
    "hit_rate_up": 55.7,
    "sharpe_like": 0.264
  }
}
```

**Reasoning:** Only 2 MF report months available. We CANNOT empirically place the alpha-decay curve yet. Recommend: keep the 60d hard veto until we have >= 12 reports, then revisit. (Risk of weight-decay change with so little data is HIGH.)

---

## Banking NIM via T-bill / KIBOR spread  —  **INCONCLUSIVE**
_Test ID: `T3a_nim_spread` · horizon: 21 day(s)_

**Claim being tested:** When (T-bill 3M − KIBOR 3M) widens >=15bps over 60d, bank basket beats baseline over next 21d.

**Sample:** `{'n': 0, 'mean': None, 'median': None, 'stdev': None, 'hit_rate_up': None, 'sharpe_like': None}`

**Baseline (random PSX picks, same horizon):** `{'n': 798, 'mean': 2.794, 'median': 0.831, 'stdev': 10.677, 'hit_rate_up': 54.3, 'sharpe_like': 0.262}`

**Detail:**
```json
{
  "compressing_stats": {
    "n": 0,
    "mean": null,
    "median": null,
    "stdev": null,
    "hit_rate_up": null,
    "sharpe_like": null
  },
  "edge_vs_baseline_pp": -2.794,
  "edge_vs_compressing_pp": 0,
  "n_wide_months": 0,
  "n_comp_months": 0
}
```

**Reasoning:** Widening months (n=0) avg=n/a vs compressing (n=0) avg=n/a vs baseline 2.79%. Edge widening-vs-baseline: -2.79pp. Edge widening-vs-compressing: 0.00pp.

---

## Banking NIM via policy-rate level  —  **PASS**
_Test ID: `T3b_nim_rate_level` · horizon: 90 day(s)_

**Claim being tested:** When policy rate is in its top quartile (>=17.50%), bank basket outperforms when it is in its bottom quartile (<=9.00%) over next 90d.

**Sample:** `{'n': 20, 'mean': 23.551, 'median': 24.83, 'stdev': 8.886, 'hit_rate_up': 100.0, 'sharpe_like': 2.65}`

**Baseline (random PSX picks, same horizon):** `{'n': 798, 'mean': 12.907, 'median': 8.872, 'stdev': 23.21, 'hit_rate_up': 66.3, 'sharpe_like': 0.556}`

**Detail:**
```json
{
  "low_rate_stats": {
    "n": 20,
    "mean": 9.906,
    "median": 15.759,
    "stdev": 7.942,
    "hit_rate_up": 80.0,
    "sharpe_like": 1.247
  },
  "edge_high_vs_low_pp": 13.645,
  "n_high_months": 20,
  "n_low_months": 20,
  "q75_pct": 17.5,
  "q25_pct": 9.0
}
```

**Reasoning:** High-rate months (n=20) fwd 90d = 23.55%. Low-rate months (n=20) fwd 90d = 9.91%. Edge = 13.64pp.

---

## PSX volume confirms direction  —  **PASS**
_Test ID: `T4_volume_regime` · horizon: 5 day(s)_

**Claim being tested:** On PSX, +1.5% days on >=1.5x median volume outperform +1.5% days on <=0.7x median volume over next 5d

**Sample:** `{'n': 4657, 'mean': 0.796, 'median': 0.012, 'stdev': 6.424, 'hit_rate_up': 50.1, 'sharpe_like': 0.124}`

**Baseline (random PSX picks, same horizon):** `{'n': 1496, 'mean': 0.244, 'median': -0.05, 'stdev': 6.223, 'hit_rate_up': 49.3, 'sharpe_like': 0.039}`

**Detail:**
```json
{
  "low_volume_up_stats": {
    "n": 734,
    "mean": 0.23,
    "median": -0.312,
    "stdev": 5.991,
    "hit_rate_up": 45.8,
    "sharpe_like": 0.038
  },
  "edge_high_minus_low_pp": 0.566
}
```

**Reasoning:** High-vol up: mean fwd5d = 0.80% (n=4657). Low-vol up: 0.23% (n=734). Edge = 0.57pp.

---

## Falling knives on PSX  —  **FAIL-INVERTED**
_Test ID: `T5_falling_knives` · horizon: 21 day(s)_

**Claim being tested:** (Rule 10) Avoid BUY when 21d return is <= -10% (don't catch falling knives). Tested as: do knives bounce on PSX?

**Sample:** `{'n': 4324, 'mean': 3.049, 'median': 2.131, 'stdev': 11.914, 'hit_rate_up': 58.4, 'sharpe_like': 0.256}`

**Baseline (random PSX picks, same horizon):** `{'n': 42794, 'mean': 1.803, 'median': 0.549, 'stdev': 11.479, 'hit_rate_up': 52.7, 'sharpe_like': 0.157}`

**Detail:**
```json
{
  "deep_knife_<=_-20pct_stats": {
    "n": 729,
    "mean": 5.606,
    "median": 4.623,
    "stdev": 14.026,
    "hit_rate_up": 63.1,
    "sharpe_like": 0.4
  },
  "edge_knife_pp": 1.246,
  "edge_deep_knife_pp": 3.803,
  "sharpe_knife_minus_base": 0.099
}
```

**Reasoning:** 21d knives: avg fwd 21d = 3.05% (n=4324) vs baseline 1.80% (n=42794). Deep knives (-20%+): 5.61% (n=729). On PSX, the verdict is: knives BOUNCE -- generic rule HURTS us

---

## Hot-sector entries on PSX  —  **WEAK-EFFECT**
_Test ID: `T6_hot_sector` · horizon: 21 day(s)_

**Claim being tested:** (Rule 11 underlying assumption) Hot sectors mean-revert on PSX, so leaning into the hottest sector underperforms.

**Sample:** `{'n': 823, 'mean': 2.232, 'median': 1.246, 'stdev': 13.499, 'hit_rate_up': 56.3, 'sharpe_like': 0.165}`

**Baseline (random PSX picks, same horizon):** `{'n': 618, 'mean': 1.793, 'median': 1.104, 'stdev': 11.404, 'hit_rate_up': 54.4, 'sharpe_like': 0.157}`

**Detail:**
```json
{
  "cold_sector_stats": {
    "n": 618,
    "mean": 1.793,
    "median": 1.104,
    "stdev": 11.404,
    "hit_rate_up": 54.4,
    "sharpe_like": 0.157
  },
  "edge_hot_minus_cold_pp": 0.439
}
```

**Reasoning:** Hot-sector buys: avg fwd 21d = 2.23% (n=823). Cold-sector buys: 1.79% (n=618). Edge = 0.44pp. No clear effect.

---

## Bi-weekly w/ 4% drift trigger  —  **FAIL**
_Test ID: `T7_rebalance` · horizon: 0 day(s)_

**Claim being tested:** Bi-weekly Phase-1 with 4% per-name drift trigger improves Sharpe / drawdown vs monthly rebalance after 100bps round-trip cost.

**Sample:** `{'backtest_horizon_days': 1829}`

**Detail:**
```json
{
  "monthly": {
    "total_return_pct": 50.46,
    "ann_sharpe": 0.432,
    "max_dd_pct": -36.75,
    "n_fills": 127
  },
  "biweekly_no_drift": {
    "total_return_pct": 9.47,
    "ann_sharpe": 0.21,
    "max_dd_pct": -35.48,
    "n_fills": 190
  },
  "biweekly_with_4pct_drift": {
    "total_return_pct": 6.07,
    "ann_sharpe": 0.188,
    "max_dd_pct": -37.79,
    "n_fills": 196
  }
}
```

**Reasoning:** Bi-weekly+drift adds turnover cost without proportional Sharpe lift (Δsharpe=-0.24, trade count 196 vs monthly 127).

---
