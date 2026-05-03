# Critical-date briefing: 2026-03-09
_V-recovery start._

**Your task:** read the structured signals below and decide what the Master Strategist would say *as of close of business* on this date. Output JSON of the form:

```json
{
  "date": "YYYY-MM-DD",
  "action": "BUY|SELL|HOLD|REDUCE|CASH",
  "conviction": "HIGH|MEDIUM|LOW",
  "top_buy": "SYMBOL or null",
  "top_short": "SYMBOL or null",
  "thesis": "2-3 sentences citing the key signals",
  "contributing_signals": ["signal-1", "signal-2", "signal-3"]
}
```

## Regime

- Regime: **CAUTION**
- Universe lookback 5d ret: -3.38%, 21d: -21.12%
- Breadth (% advancing): `30.6%`
- Exposure multiplier: `1.0`

## Phase-1 strategy signal

- market_risk_on: `False`
- Selected: _(none -- Phase-1 has no entry today)_

## Policy rate

- SBP policy rate: **11.5%**
- Cycle phase: `n/a`, days since last decision: `n/a`

## Macro KPIs

- `kibor_3m_pct`: `11.687`
- `tbill_3m_pct`: `11.19`
- `cpi_yoy_pct`: `7.3`
- `reserves_total_usd_mn`: `20119.0`
- `kse100_ret_5d`: `-0.052065053338720246`
- `kse100_ret_21d`: `-0.06047885891110738`

## Active events

- _(none)_

## Macro drivers

- `oil_up` (STRONG)

## Mutual-fund flows (last 30d / 180d)

- Universe net flow PKR mn (30d): `n/a`
- Universe net flow PKR mn (180d): `n/a`
- Data freshness: `67d`

## FIPI flows

- `net_5d_pkr_mn`: `None`

## LLM 5d predictions (top-K detail)


## Pre-computed playbook analogues (from rules engine)

- _(no cases fired -- this is informational, you can still take a stance from raw signals)_

## News sentiment

- `tilt`: `None`

