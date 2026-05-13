# Predictions log scoreboard

Scored 237 predictions across 35 symbols, 3 models

## Direction accuracy
| Direction | n | avg actual % | hit % |
|-----------|---|--------------|-------|
| BEARISH  |  43 | +2.14% |   33% |
| BULLISH  |  30 | +2.23% |   63% |
| NEUTRAL  | 164 | +1.94% |   57% |

## Action P&L (vs HOLD baseline)
| Action | n | avg actual % | edge vs HOLD |
|--------|---|--------------|--------------|
| ADD    |  19 | +2.73% | +0.68% |
| AVOID  |  41 | +2.29% | -1.14% |
| BUY    |   7 | +2.59% | +1.30% |
| HOLD   | 169 | +1.87% | +0.00% |
| TRIM   |   1 | -2.71% | +1.36% |

## Model comparison
| Model | n | dir hit % | action P&L | edge vs HOLD |
|-------|---|-----------|------------|--------------|
| claude-haiku-4-5       | 128 | 51.6% | +1.11% | -0.16% |
| claude-sonnet-4-5      |  71 | 60.6% | +0.01% | -0.13% |
| rule-based-v1          |  38 | 44.7% | +1.89% | +0.16% |

## Conviction calibration (BULLISH actual return by conviction)
| Conviction | n | n bullish | bull avg | n bearish | bear avg |
|------------|---|-----------|----------|-----------|----------|
| HIGH     |   7 |   6 | +1.72% |   1 | -11.08% |
| LOW      |  97 |   0 |    -    |   3 | +0.74% |
| MEDIUM   | 133 |  24 | +2.36% |  39 | +2.59% |

## Top 10 symbols by avg action P&L
| Symbol | n | dir hit % | avg P&L |
|--------|---|-----------|---------|
| TRG    |   5 |  0.0% | +5.84% |
| KEL    |   5 |  0.0% | +5.09% |
| LUCK   |   5 | 40.0% | +3.00% |
| FCCL   |   9 | 33.3% | +2.18% |
| UBL    |   5 | 20.0% | +2.15% |
| PPL    |   9 | 22.2% | +1.86% |
| APL    |   9 | 100.0% | +1.73% |
| OGDC   |   9 | 55.6% | +1.69% |
| EPCL   |   9 | 33.3% | +1.68% |
| MLCF   |   9 | 44.4% | +1.65% |

## Bottom 10 symbols by avg action P&L
| Symbol | n | dir hit % | avg P&L |
|--------|---|-----------|---------|
| KAPCO  |   5 | 80.0% | +0.06% |
| EFERT  |   5 | 80.0% | +0.06% |
| ENGROH |   5 | 80.0% | -0.09% |
| BAHL   |   5 | 80.0% | -0.12% |
| POL    |   9 | 88.9% | -0.14% |
| ATRL   |   5 | 80.0% | -0.52% |
| MEBL   |   9 | 88.9% | -0.62% |
| HUBC   |   9 | 77.8% | -1.24% |
| PSO    |   9 | 66.7% | -1.27% |
| INDU   |   5 | 20.0% | -2.68% |
