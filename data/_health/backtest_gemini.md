# Layer 2 -- Gemini brain backtest

_Run at 2026-05-03T13:24:17_

Model: `gemini-2.5-flash` via Google Gemini Free Tier (API key from env (1):GEMINI_API_KEY).
Sample: 104 weekly dates (2024-05-03 -> 2026-05-01, weekday=4).

## Headline

| Metric | Value |
|---|---|
| Dates evaluated | 104 |
| Valid LLM decisions | 104 |
| Errors (replay or LLM) | 0 |
| **Direction hit-rate (5d)** | 50.5% (52/103) |
| **Mean top-pick alpha vs universe (5d)** | +1.58pp (10 scored) |
| Mean fwd-5d on top_buy (10 scored) | +2.11% |
| Mean fwd-5d on top_short (3 scored) | +0.07% |

## Action distribution

| Action | Count |
|---|---:|
| `CAUTIOUS` | 68 |
| `NORMAL` | 15 |
| `CASH` | 14 |
| `DEFENSIVE` | 7 |

## Per-date detail

| Date | Action | Conv | Top buy | Buy 5d | Top short | Short 5d | Universe 5d | Hit |
|---|---|---|---|---:|---|---:|---:|---|
| 2024-05-03 | CAUTIOUS | LOW |  | n/a |  | n/a | +2.2% | MISS |
| 2024-05-10 | CASH | MEDIUM |  | n/a |  | n/a | +2.1% | MISS |
| 2024-05-17 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.9% | HIT |
| 2024-05-24 | CASH | MEDIUM |  | n/a |  | n/a | -0.2% | HIT |
| 2024-05-31 | CAUTIOUS | LOW |  | n/a |  | n/a | -2.2% | MISS |
| 2024-06-07 | DEFENSIVE | MEDIUM |  | n/a |  | n/a | +3.1% | MISS |
| 2024-06-14 | CAUTIOUS | LOW |  | n/a |  | n/a | +1.5% | HIT |
| 2024-06-21 | NORMAL | LOW |  | n/a |  | n/a | -0.5% | HIT |
| 2024-06-28 | CAUTIOUS | LOW |  | n/a |  | n/a | +2.7% | MISS |
| 2024-07-05 | CASH | MEDIUM |  | n/a |  | n/a | +0.0% | MISS |
| 2024-07-12 | CAUTIOUS | LOW |  | n/a |  | n/a | -2.0% | MISS |
| 2024-07-19 | CASH | LOW |  | n/a |  | n/a | -2.9% | HIT |
| 2024-07-26 | DEFENSIVE | LOW |  | n/a |  | n/a | +0.0% | MISS |
| 2024-08-02 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.0% | HIT |
| 2024-08-09 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.5% | HIT |
| 2024-08-16 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +1.7% | HIT |
| 2024-08-23 | CASH | LOW |  | n/a |  | n/a | -0.0% | HIT |
| 2024-08-30 | CASH | LOW |  | n/a |  | n/a | +0.5% | MISS |
| 2024-09-06 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.1% | HIT |
| 2024-09-13 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.3% | HIT |
| 2024-09-20 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.5% | HIT |
| 2024-09-27 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +1.8% | HIT |
| 2024-10-04 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +2.3% | MISS |
| 2024-10-11 | NORMAL | MEDIUM |  | n/a |  | n/a | -0.4% | HIT |
| 2024-10-18 | CAUTIOUS | LOW |  | n/a |  | n/a | +6.3% | MISS |
| 2024-10-25 | NORMAL | MEDIUM |  | n/a |  | n/a | +1.2% | HIT |
| 2024-11-01 | CAUTIOUS | LOW |  | n/a |  | n/a | +3.1% | MISS |
| 2024-11-08 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +1.7% | HIT |
| 2024-11-15 | NORMAL | LOW |  | n/a |  | n/a | +2.1% | MISS |
| 2024-11-22 | CAUTIOUS | LOW |  | n/a |  | n/a | +4.7% | MISS |
| 2024-11-29 | CASH | LOW |  | n/a |  | n/a | +7.5% | MISS |
| 2024-12-06 | CAUTIOUS | LOW |  | n/a |  | n/a | +4.8% | MISS |
| 2024-12-13 | CAUTIOUS | LOW |  | n/a |  | n/a | -4.3% | MISS |
| 2024-12-20 | CAUTIOUS | LOW |  | n/a |  | n/a | +5.5% | MISS |
| 2024-12-27 | CAUTIOUS | LOW |  | n/a |  | n/a | +3.6% | MISS |
| 2025-01-03 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -4.3% | MISS |
| 2025-01-10 | DEFENSIVE | LOW |  | n/a |  | n/a | +1.9% | MISS |
| 2025-01-17 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.6% | HIT |
| 2025-01-24 | CAUTIOUS | LOW |  | n/a |  | n/a | -0.5% | HIT |
| 2025-01-31 | CASH | LOW |  | n/a |  | n/a | -2.3% | HIT |
| 2025-02-07 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +1.5% | HIT |
| 2025-02-14 | NORMAL | LOW |  | n/a |  | n/a | +1.4% | HIT |
| 2025-02-21 | CASH | HIGH |  | n/a |  | n/a | +0.1% | MISS |
| 2025-02-28 | CASH | MEDIUM |  | n/a |  | n/a | +1.7% | MISS |
| 2025-03-07 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.1% | HIT |
| 2025-03-14 | CAUTIOUS | LOW |  | n/a |  | n/a | +1.9% | HIT |
| 2025-03-21 | NORMAL | MEDIUM |  | n/a |  | n/a | +0.5% | HIT |
| 2025-03-28 | CAUTIOUS | LOW |  | n/a |  | n/a | -3.5% | MISS |
| 2025-04-04 | NORMAL | MEDIUM |  | n/a |  | n/a | -3.3% | MISS |
| 2025-04-11 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.6% | HIT |
| 2025-04-18 | CAUTIOUS | LOW |  | n/a |  | n/a | -1.9% | HIT |
| 2025-04-25 | CAUTIOUS | LOW |  | n/a |  | n/a | -1.1% | HIT |
| 2025-05-02 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -6.7% | MISS |
| 2025-05-09 | CASH | LOW |  | n/a |  | n/a | +12.4% | MISS |
| 2025-05-16 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +0.0% | HIT |
| 2025-05-23 | CAUTIOUS | LOW |  | n/a |  | n/a | +0.5% | HIT |
| 2025-05-30 | CAUTIOUS | LOW |  | n/a |  | n/a | +1.8% | HIT |
| 2025-06-06 | CAUTIOUS | MEDIUM | DGKC | +2.5% |  | n/a | +0.4% | HIT |
| 2025-06-13 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.3% | HIT |
| 2025-06-20 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +3.7% | MISS |
| 2025-06-27 | CAUTIOUS | MEDIUM | LUCK | -0.8% |  | n/a | +5.7% | MISS |
| 2025-07-04 | CAUTIOUS | MEDIUM | LUCK | -0.3% |  | n/a | +1.7% | HIT |
| 2025-07-11 | CAUTIOUS | MEDIUM | DGKC | +0.6% | HUBC | +2.7% | +1.8% | HIT |
| 2025-07-18 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -0.1% | HIT |
| 2025-07-25 | NORMAL | MEDIUM |  | n/a |  | n/a | +1.4% | HIT |
| 2025-08-01 | CAUTIOUS | LOW |  | n/a |  | n/a | +2.6% | MISS |
| 2025-08-08 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +2.1% | MISS |
| 2025-08-15 | NORMAL | MEDIUM |  | n/a |  | n/a | +2.2% | MISS |
| 2025-08-22 | CAUTIOUS | LOW |  | n/a |  | n/a | -0.3% | HIT |
| 2025-08-29 | NORMAL | MEDIUM | DGKC | +14.4% |  | n/a | +3.9% | MISS |
| 2025-09-05 | NORMAL | MEDIUM |  | n/a |  | n/a | +0.4% | HIT |
| 2025-09-12 | CAUTIOUS | LOW |  | n/a |  | n/a | +2.6% | MISS |
| 2025-09-19 | DEFENSIVE | MEDIUM |  | n/a |  | n/a | +2.8% | MISS |
| 2025-09-26 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +3.8% | MISS |
| 2025-10-03 | NORMAL | MEDIUM | LUCK | -5.4% |  | n/a | -3.8% | MISS |
| 2025-10-10 | CAUTIOUS | LOW |  | n/a |  | n/a | -0.4% | HIT |
| 2025-10-17 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.9% | HIT |
| 2025-10-24 | CASH | LOW |  | n/a |  | n/a | -1.6% | HIT |
| 2025-10-31 | CAUTIOUS | LOW |  | n/a |  | n/a | -1.3% | HIT |
| 2025-11-07 | CAUTIOUS | LOW |  | n/a |  | n/a | +1.1% | HIT |
| 2025-11-14 | NORMAL | MEDIUM |  | n/a |  | n/a | +0.1% | HIT |
| 2025-11-21 | CASH | LOW |  | n/a |  | n/a | +2.0% | MISS |
| 2025-11-28 | NORMAL | MEDIUM | DGKC | +3.7% |  | n/a | +1.5% | HIT |
| 2025-12-05 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +4.0% | MISS |
| 2025-12-12 | CASH | LOW |  | n/a |  | n/a | +1.2% | MISS |
| 2025-12-19 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +1.2% | HIT |
| 2025-12-26 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +2.6% | MISS |
| 2026-01-02 | CAUTIOUS | MEDIUM | MCB | +12.9% | HUBC | +3.3% | +3.2% | MISS |
| 2026-01-09 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +0.7% | HIT |
| 2026-01-16 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +2.3% | MISS |
| 2026-01-23 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -2.0% | MISS |
| 2026-01-30 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.2% | HIT |
| 2026-02-06 | CAUTIOUS | MEDIUM | MCB | +0.4% |  | n/a | -2.4% | MISS |
| 2026-02-13 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -4.8% | MISS |
| 2026-02-20 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -2.8% | MISS |
| 2026-02-27 | DEFENSIVE | MEDIUM | MCB | -7.0% | HUBC | -5.8% | -6.6% | HIT |
| 2026-03-06 | DEFENSIVE | MEDIUM |  | n/a |  | n/a | -1.7% | HIT |
| 2026-03-13 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -1.1% | HIT |
| 2026-03-20 | CAUTIOUS | LOW |  | n/a |  | n/a | -4.0% | MISS |
| 2026-03-27 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +0.3% | HIT |
| 2026-04-03 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | +11.8% | MISS |
| 2026-04-10 | NORMAL | LOW |  | n/a |  | n/a | +3.6% | MISS |
| 2026-04-17 | CAUTIOUS | MEDIUM |  | n/a |  | n/a | -2.5% | MISS |
| 2026-04-24 | DEFENSIVE | MEDIUM |  | n/a |  | n/a | n/a |  |

