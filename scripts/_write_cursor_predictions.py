"""Generate Cursor-reasoned 5-day predictions for all 35 universe symbols.

Anthropic credits are exhausted, so `scripts/generate_predictions.py` falls
back to the rule-based engine. This script replaces that with hand-encoded
reasoning that uses the SAME inputs Claude would see (per-stock data + macro)
and writes records into `data/predictions_log.json` matching the exact
schema produced by `generate_one()`.

Each prediction is a 5-trading-day forecast with the standard fields:
  prediction_id, generated_at, symbol, sector, model, horizon_trading_days,
  entry_price_pkr, direction, conviction, expected_return_5d_{low,mid,high}_pct,
  suggested_action, suggested_stop_pkr, suggested_target_pkr, rationale,
  key_drivers, key_risks, macro_tailwinds, macro_headwinds,
  macro_impact, mpc_alert, mpc_cap_applied, data_snapshot.

`outcome` is intentionally left absent — `scripts/check_predictions.py` will
fill it on the next EOD run.
"""

import sys
import json
import os
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

PER_STOCK = Path('data/_strategist/_per_stock_2026-05-11.json')
LOG_PATH = Path('data/predictions_log.json')
OHLCV_DIR = Path('data/ohlcv')
TODAY = '2026-05-11'
MODEL = 'cursor-claude-sonnet-4-5-manual'
HORIZON = 5
NOW = datetime.now(ZoneInfo('Asia/Karachi')).isoformat()


def _last_close_from_ohlcv(sym: str, asof: str | None = None) -> float | None:
    """Fallback close from data/ohlcv/<SYM>.parquet when the per-stock
    snapshot lacks close_pkr. Returns None if file missing or empty."""
    fp = OHLCV_DIR / f'{sym}.parquet'
    if not fp.exists():
        return None
    try:
        df = pd.read_parquet(fp)
        if df.empty or 'close' not in df.columns:
            return None
        df = df.sort_values('date')
        if asof:
            cut = df[df['date'] <= pd.to_datetime(asof)]
            if not cut.empty:
                return float(cut.iloc[-1]['close'])
        return float(df.iloc[-1]['close'])
    except Exception:
        return None

per_stock: dict[str, dict] = json.loads(
    PER_STOCK.read_bytes().decode('utf-8', 'replace'))

# ---------------------------------------------------------------------------
# Per-stock prediction reasoning. Every key has the FULL set of required
# prediction fields. Numbers are explicit (not derived) so the reasoning is
# auditable. Rule of thumb for expected_return_5d_mid_pct:
#   BULLISH HIGH:  +1.5 to +3.0%
#   BULLISH MED:   +0.6 to +1.5%
#   NEUTRAL:       -0.5 to +0.5%
#   BEARISH MED:   -0.6 to -1.5%
#   BEARISH HIGH:  -1.5 to -3.0%
# ---------------------------------------------------------------------------
P: dict[str, dict] = {

    # ============================================================
    # E&P (Oil & Gas)  — Brent floor + IMF May 15 binary
    # ============================================================
    'OGDC': dict(
        direction='BULLISH', conviction='MEDIUM',
        ret=(0.5, 1.8, 3.2), action='BUY',
        rationale=(
            'OGDC HIGH-quality (85.5/100) E&P with +30% upside to fair value '
            '(BUY_VALUE HIGH confidence). Phase-1 #3 with mom +17% over 150d. '
            'Volume confirmed 3d breakout. US-Iran tension has put a floor under '
            'Brent (currently $104, still elevated despite -9% mean-reversion in '
            '5d). IMF May 15 talks could unlock further upside on a successful '
            'review (state-owned name benefits from PKR strength).'
        ),
        drivers=[
            'BUY_VALUE +30% upside (HIGH confidence, P/B + P/E)',
            'Quality 85.5/100 (HIGH band)',
            'Phase-1 momentum +17% (rank #3)',
            'Volume-confirmed breakout in last 3 sessions',
            'Brent geopolitical floor ~$100',
        ],
        risks=[
            'Earnings momentum EROSION (yoy -7%)',
            'IMF delay/hawkish demands → state-owned name sells off first',
            'Brent mean-reversion to $90 if US-Iran de-escalates',
        ],
        tail=['Brent crude floor (US-Iran)',
              'IMF May 15 (potential PKR strength)'],
        head=['Earnings deceleration (yoy -7%)'],
        stop_pct=-0.04, target_pct=0.07,
    ),
    'ATRL': dict(
        direction='BULLISH', conviction='MEDIUM',
        ret=(0.4, 1.5, 2.8), action='BUY',
        rationale=(
            'ATRL is Phase-1 #2 (mom +20% over 150d), HIGH quality, FAIR value '
            'with +21% upside. MF funds are initiating positions — 7 new fund '
            'positions in 30d. Mgmt outlook (filed Apr 30) flags Gulf risks but '
            'expects refining margins to hold via indigenous crude advantage. '
            'Material info filed May 4 — watch for Q3 earnings update.'
        ),
        drivers=[
            'Phase-1 #2 (mom +20%)',
            'FAIR value with +21% upside',
            'HIGH quality band',
            'MF initiating: 7 new fund positions in 30d',
            'Mgmt outlook tone +0.15 (mildly positive)',
        ],
        risks=[
            'Refining margin compression if Brent spikes again',
            'Material information filing may signal earnings miss',
            'Gulf shipping insurance premium increase',
        ],
        tail=['Indigenous crude supply', 'MF fund initiation cluster'],
        head=['Refining margin volatility'],
        stop_pct=-0.04, target_pct=0.06,
    ),
    'PPL': dict(
        direction='BULLISH', conviction='MEDIUM',
        ret=(0.3, 1.4, 2.6), action='BUY',
        rationale=(
            'Deep value play: BUY_VALUE +52% upside, HIGH quality. Phase-1 #5 '
            '(mom +13%). Faiz X-1 well producing 3.6 bcf/d (positive sentiment '
            'driver in news cycle). Earnings DECELERATING but balance sheet '
            'strong. Same Brent floor argument as OGDC.'
        ),
        drivers=[
            'BUY_VALUE +52% upside (HIGH confidence)',
            'HIGH quality',
            'Phase-1 #5 (mom +13%)',
            'Faiz X-1 well (3.6 bcf/d) — positive news catalyst',
            'Brent crude floor ~$100',
        ],
        risks=[
            'Earnings DECELERATING',
            'Smaller market cap than OGDC — less FX-flow support',
            'IMF delay impact (PKR weakness → Brent translation lower)',
        ],
        tail=['Brent crude floor', 'New well production ramp'],
        head=['Earnings deceleration'],
        stop_pct=-0.04, target_pct=0.06,
    ),
    'POL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.8, 0.2, 1.2), action='HOLD',
        rationale=(
            'Smaller E&P. SELL_VALUE -12% (slight overvaluation), DECELERATING '
            'earnings, Phase-1 mom -11%. Less Brent leverage than OGDC/PPL. '
            'No edge for 5-day window.'
        ),
        drivers=['Brent crude support'],
        risks=['SELL_VALUE -12%', 'Earnings DECELERATING',
               'Phase-1 momentum negative'],
        tail=['Brent crude'],
        head=['Phase-1 negative momentum', 'IMF binary risk'],
        stop_pct=-0.04, target_pct=0.04,
    ),
    'MARI': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-2.0, -0.8, 0.4), action='HOLD',
        rationale=(
            'SELL_VALUE -43% (significantly overvalued), HIGH quality but '
            'EROSION earnings, Phase-1 mom -18%. Despite E&P sector tailwind, '
            'valuation premium is too high for fresh entry.'
        ),
        drivers=[],
        risks=['SELL_VALUE -43% (deep premium to fair value)',
               'EROSION earnings', 'Phase-1 mom -18%'],
        tail=['E&P sector (mild)'],
        head=['Severe valuation premium', 'Earnings erosion'],
        stop_pct=-0.04, target_pct=0.03,
    ),

    # ============================================================
    # OMC / Refining — squeezed by oil volatility
    # ============================================================
    'PSO': dict(
        direction='BEARISH', conviction='MEDIUM',
        ret=(-2.2, -1.0, 0.2), action='HOLD',
        rationale=(
            'OMC sector under structural pressure. RECOVERING earnings but '
            'FAIR value (+14% upside is small comfort). LOW quality. Phase-1 '
            'mom -29%. US-Iran oil volatility hurts inventory mark-to-market. '
            'Largest single creditor in circular debt — sentiment overhang.'
        ),
        drivers=['RECOVERING earnings'],
        risks=[
            'Phase-1 mom -29%', 'LOW quality',
            'Oil volatility → inventory MTM losses',
            'Circular debt overhang',
        ],
        tail=['Circular debt resolution potential'],
        head=['Oil volatility', 'Phase-1 negative momentum',
              'LOW quality band'],
        stop_pct=-0.05, target_pct=0.03,
    ),
    'APL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-1.0, -0.2, 0.6), action='HOLD',
        rationale=(
            'SELL_VALUE -14%, RECOVERING earnings, HIGH quality, but Phase-1 '
            'mom +7% (positive but weak). Same OMC pressures as PSO but better '
            'fundamentals. Hold.'
        ),
        drivers=['RECOVERING earnings', 'HIGH quality'],
        risks=['SELL_VALUE -14%', 'OMC sector pressure'],
        tail=['Recovering earnings'],
        head=['OMC sector pressure', 'Oil volatility'],
        stop_pct=-0.04, target_pct=0.04,
    ),

    # ============================================================
    # Banking — IMF May 15 binary, NIM compression
    # ============================================================
    'MCB': dict(
        direction='NEUTRAL', conviction='MEDIUM',
        ret=(-0.4, 0.4, 1.4), action='HOLD',
        rationale=(
            'Phase-1 would-pick #5. SELL_VALUE -14% (slight premium), HIGH '
            'quality (90.8/100 — top of universe), but EROSION earnings (-7.6%). '
            'IMF May 15 = direct catalyst. On a successful review the bank '
            'rallies +1-2%; on delay sells off -1-2%. Hold through the binary.'
        ),
        drivers=['HIGH quality (90.8/100)', 'Phase-1 #6 (mom +10%)',
                 'IMF positive catalyst potential'],
        risks=['SELL_VALUE -14%', 'EROSION earnings (yoy -7.6%)',
               'IMF delay risk'],
        tail=['IMF success scenario', 'Quality leader'],
        head=['NIM compression on rate cuts', 'Earnings erosion'],
        stop_pct=-0.03, target_pct=0.04,
    ),
    'UBL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.6, 0.3, 1.2), action='HOLD',
        rationale=(
            'Composite verdict BUY (synthesizer score +9), but SELL_VALUE -19% '
            'overrides. ACCELERATING earnings is positive. Phase-1 mom +3% '
            '(weak). IMF binary same as MCB.'
        ),
        drivers=['ACCELERATING earnings', 'Composite verdict BUY',
                 'HIGH quality'],
        risks=['SELL_VALUE -19%', 'IMF binary risk'],
        tail=['Earnings acceleration', 'IMF success potential'],
        head=['Valuation premium', 'IMF delay risk'],
        stop_pct=-0.03, target_pct=0.04,
    ),
    'HBL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.5, 0.3, 1.1), action='HOLD',
        rationale=(
            'FAIR value with +16% upside (best valuation in big-banks). '
            'ACCELERATING earnings. HIGH quality. But Phase-1 mom -2% (flat). '
            'IMF risk dominant.'
        ),
        drivers=['ACCELERATING earnings', 'FAIR value with +16% upside',
                 'HIGH quality'],
        risks=['IMF binary risk', 'Phase-1 momentum flat'],
        tail=['Earnings acceleration'],
        head=['IMF delay risk'],
        stop_pct=-0.03, target_pct=0.04,
    ),
    'NBP': dict(
        direction='NEUTRAL', conviction='MEDIUM',
        ret=(-0.8, 0.5, 1.8), action='HOLD',
        rationale=(
            'Highest value upside in universe (+101% to fair value, BUY_VALUE '
            'HIGH confidence). HIGH quality, RECOVERING earnings. BUT state-'
            'owned bank is most exposed to a hawkish IMF outcome (govt-debt '
            'balance sheet). Phase-1 mom -12% short-term. WATCH not BUY until '
            'IMF May 15 outcome is known.'
        ),
        drivers=['BUY_VALUE +101% upside (highest in universe)',
                 'RECOVERING earnings', 'HIGH quality'],
        risks=['Phase-1 mom -12%', 'State-owned: highest IMF exposure',
               'Govt balance-sheet risk'],
        tail=['Extreme value cushion'],
        head=['IMF binary risk (state-owned)'],
        stop_pct=-0.04, target_pct=0.05,
    ),
    'MEBL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.7, 0.2, 1.0), action='HOLD',
        rationale=(
            'HIGH quality but SELL_VALUE -41%, EROSION earnings, Phase-1 mom '
            '+6% (weak). Sharia banking. Wait for IMF clarity.'
        ),
        drivers=['HIGH quality', 'Sharia premium'],
        risks=['SELL_VALUE -41%', 'EROSION earnings'],
        tail=['Quality'],
        head=['Valuation premium'],
        stop_pct=-0.03, target_pct=0.03,
    ),
    'BAHL': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-1.5, -0.6, 0.4), action='HOLD',
        rationale=(
            'EROSION earnings, SELL_VALUE -10%, Phase-1 mom -22% (worst-tier '
            'bank). IMF binary unfavourable for weak names.'
        ),
        drivers=[],
        risks=['Phase-1 mom -22%', 'EROSION earnings', 'IMF binary risk'],
        tail=[],
        head=['Phase-1 momentum', 'Earnings erosion'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'FABL': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-1.6, -0.7, 0.3), action='HOLD',
        rationale=(
            'EROSION earnings, SELL_VALUE -27%, Phase-1 mom -5%. Smaller bank, '
            'IMF risk amplified.'
        ),
        drivers=[],
        risks=['SELL_VALUE -27%', 'EROSION earnings'],
        tail=[],
        head=['Bank IMF risk', 'Earnings erosion'],
        stop_pct=-0.04, target_pct=0.03,
    ),

    # ============================================================
    # Power — circular debt overhang, low fundamentals data
    # ============================================================
    'NPL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-1.0, 0.3, 1.5), action='HOLD',
        rationale=(
            'Phase-1 #1 (mom +72% — outlier strong) but SELL_VALUE -20% and '
            'ACCELERATING earnings. Defensive Power name. Recent 1d -3% '
            'reversal. Hold existing, no fresh entry on the run-up.'
        ),
        drivers=['Phase-1 #1 (mom +72%)', 'ACCELERATING earnings'],
        risks=['SELL_VALUE -20%', 'Recent reversal', 'Power circular debt'],
        tail=['Strong momentum'],
        head=['Valuation', 'Recent reversal'],
        stop_pct=-0.05, target_pct=0.05,
    ),
    'KEL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-1.0, 0.2, 1.4), action='HOLD',
        rationale=(
            'Phase-1 would-pick #4. SELL_VALUE -60%, LOW quality, EROSION '
            'earnings. Power circular debt overhang. Volume breakout in 3d. '
            'Hold only.'
        ),
        drivers=['Phase-1 #4', 'Volume breakout'],
        risks=['SELL_VALUE -60%', 'LOW quality', 'EROSION earnings'],
        tail=['Volume confirmation'],
        head=['Valuation', 'Quality'],
        stop_pct=-0.05, target_pct=0.05,
    ),
    'HUBC': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-1.0, 0.4, 1.6), action='HOLD',
        rationale=(
            'BUY_VALUE +41% upside but UNKNOWN quality (insufficient data). '
            'Phase-1 mom -9%. Power IPP — direct circular-debt beneficiary on '
            'any fresh resolution. Watch for catalyst.'
        ),
        drivers=['BUY_VALUE +41% upside',
                 'IPP — circular debt resolution beneficiary'],
        risks=['UNKNOWN quality', 'Phase-1 negative'],
        tail=['Circular debt resolution potential'],
        head=['Quality unknown'],
        stop_pct=-0.05, target_pct=0.05,
    ),
    'KAPCO': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-1.8, -0.7, 0.4), action='HOLD',
        rationale=(
            'BUY_VALUE +31% upside, MEDIUM quality, RECOVERING earnings — but '
            'Phase-1 mom -25% is a major red flag. Classic value-trap risk in '
            'IPP sector with circular-debt overhang.'
        ),
        drivers=['BUY_VALUE +31%', 'RECOVERING earnings'],
        risks=['Phase-1 mom -25%', 'Value-trap risk', 'Circular debt'],
        tail=['Value cushion'],
        head=['Phase-1 deeply negative'],
        stop_pct=-0.04, target_pct=0.03,
    ),

    # ============================================================
    # Cement — demand weak, rate-cycle dependent
    # ============================================================
    'LUCK': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-1.6, -0.6, 0.4), action='HOLD',
        rationale=(
            'SELL_VALUE -23%, DECELERATING earnings. Phase-1 mom -6%. Cement '
            'demand under pressure (LSM index flat YoY).'
        ),
        drivers=['HIGH quality'],
        risks=['SELL_VALUE -23%', 'DECELERATING earnings',
               'Cement demand weakness'],
        tail=[],
        head=['Cement sector demand'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'DGKC': dict(
        direction='BEARISH', conviction='MEDIUM',
        ret=(-2.2, -1.0, 0.2), action='HOLD',
        rationale=(
            'SELL_VALUE -99% (extreme premium — price double fair value). '
            'RECOVERING earnings but LOW quality. Phase-1 mom -30%. Cement '
            'demand weak.'
        ),
        drivers=['RECOVERING earnings'],
        risks=['SELL_VALUE -99% (severe premium)',
               'LOW quality', 'Phase-1 mom -30%'],
        tail=['Earnings recovery'],
        head=['Severe valuation premium', 'Phase-1 deeply negative'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'FCCL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-1.4, -0.4, 0.6), action='HOLD',
        rationale=(
            'ACCELERATING earnings (positive) but SELL_VALUE -37% overrides. '
            'Phase-1 mom -16%. Cement demand soft.'
        ),
        drivers=['ACCELERATING earnings', 'HIGH quality'],
        risks=['SELL_VALUE -37%', 'Phase-1 negative',
               'Cement demand weakness'],
        tail=['Earnings acceleration'],
        head=['Valuation premium'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'MLCF': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-1.6, -0.6, 0.4), action='HOLD',
        rationale=(
            'ACCELERATING earnings but SELL_VALUE -28%, Phase-1 mom -20%. '
            'Cement demand weak.'
        ),
        drivers=['ACCELERATING earnings'],
        risks=['SELL_VALUE -28%', 'Phase-1 mom -20%'],
        tail=['Earnings acceleration'],
        head=['Phase-1 deeply negative', 'Cement demand'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'KOHC': dict(
        direction='BEARISH', conviction='MEDIUM',
        ret=(-1.8, -0.8, 0.2), action='HOLD',
        rationale=(
            'DECELERATING earnings, SELL_VALUE -10%, Phase-1 mom -22%. '
            'Earnings calendar shows next event May 21 — pre-results blackout '
            'pressure.'
        ),
        drivers=['HIGH quality'],
        risks=['DECELERATING earnings', 'Phase-1 mom -22%',
               'Pre-earnings blackout May 21'],
        tail=[],
        head=['Earnings deceleration', 'Phase-1 negative'],
        stop_pct=-0.04, target_pct=0.03,
    ),

    # ============================================================
    # Fertilizer — agri/budget tailwind potential
    # ============================================================
    'FFC': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.5, 0.3, 1.1), action='HOLD',
        rationale=(
            'STEADY earnings (best-in-class), HIGH quality, FAIR value with '
            '-7% (slight premium). Phase-1 mom +10% (positive). Defensive '
            'in current regime.'
        ),
        drivers=['STEADY earnings', 'HIGH quality', 'Phase-1 +10%'],
        risks=['FAIR value with slight premium'],
        tail=['Defensive', 'Steady earnings'],
        head=['Slight overvaluation'],
        stop_pct=-0.03, target_pct=0.04,
    ),
    'FATIMA': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.7, 0.4, 1.5), action='HOLD',
        rationale=(
            'BUY_VALUE +35% upside, HIGH quality, DECELERATING earnings. '
            'Phase-1 mom -5%. Fertilizer FIPI net inflow May 8. Watch only.'
        ),
        drivers=['BUY_VALUE +35%', 'HIGH quality', 'Fertilizer FIPI inflow'],
        risks=['DECELERATING earnings', 'Phase-1 mildly negative'],
        tail=['Value cushion'],
        head=['Earnings deceleration'],
        stop_pct=-0.04, target_pct=0.04,
    ),
    'EFERT': dict(
        direction='BEARISH', conviction='LOW',
        ret=(-1.5, -0.6, 0.4), action='HOLD',
        rationale=(
            'EROSION earnings, SELL_VALUE -32%, Phase-1 mom -15%. Lagging '
            'peer.'
        ),
        drivers=[],
        risks=['SELL_VALUE -32%', 'EROSION earnings', 'Phase-1 mom -15%'],
        tail=[],
        head=['Earnings erosion', 'Phase-1 negative'],
        stop_pct=-0.04, target_pct=0.03,
    ),

    # ============================================================
    # Conglomerate / Chem
    # ============================================================
    'EPCL': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.8, 0.3, 1.4), action='HOLD',
        rationale=(
            'Phase-1 would-pick #5. JUNK quality, EROSION earnings. Mgmt '
            'outlook (Apr 30) acknowledges commodity correction risk. '
            'Earnings May 18 — pre-blackout. Volume breakout in 3d.'
        ),
        drivers=['Phase-1 #5', 'Volume breakout'],
        risks=['JUNK quality', 'EROSION earnings',
               'Pre-earnings blackout May 18'],
        tail=['Volume confirmation'],
        head=['Quality', 'Earnings erosion'],
        stop_pct=-0.05, target_pct=0.04,
    ),
    'LOTCHEM': dict(
        direction='BEARISH', conviction='MEDIUM',
        ret=(-2.5, -1.2, 0.0), action='HOLD',
        rationale=(
            'Composite verdict TRIM. Brent -9% in 5d narrows PVC margins. LOW '
            'quality, EROSION earnings, Phase-1 mom -2%. On short-candidate '
            'list (low conviction).'
        ),
        drivers=[],
        risks=['Composite TRIM', 'PVC margin pressure',
               'LOW quality', 'EROSION earnings'],
        tail=[],
        head=['Brent → PVC margin compression', 'Composite TRIM'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'ENGROH': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.7, 0.3, 1.3), action='HOLD',
        rationale=(
            'Insufficient earnings data — UNKNOWN quality, NO_SIGNAL value. '
            'Phase-1 mom +8%. Hold pending more disclosure.'
        ),
        drivers=['Phase-1 +8%'],
        risks=['Quality UNKNOWN', 'No value signal'],
        tail=['Phase-1 weakly positive'],
        head=['Data gap'],
        stop_pct=-0.04, target_pct=0.04,
    ),

    # ============================================================
    # Auto / Consumer / Pharma / Tech
    # ============================================================
    'INDU': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-0.7, 0.2, 1.1), action='HOLD',
        rationale=(
            'STEADY earnings, HIGH quality, but SELL_VALUE -27%. Phase-1 mom '
            '-9%. Auto demand recovering but valuation rich.'
        ),
        drivers=['STEADY earnings', 'HIGH quality'],
        risks=['SELL_VALUE -27%', 'Phase-1 negative'],
        tail=['Quality', 'Steady earnings'],
        head=['Valuation premium'],
        stop_pct=-0.03, target_pct=0.04,
    ),
    'COLG': dict(
        direction='BEARISH', conviction='MEDIUM',
        ret=(-2.0, -0.9, 0.2), action='HOLD',
        rationale=(
            'Composite verdict TRIM. SELL_VALUE -69% (deeply overvalued). '
            'DECELERATING earnings, Phase-1 mom -16%. Consumer discretionary '
            'squeezed by inflation lag.'
        ),
        drivers=['HIGH quality'],
        risks=['SELL_VALUE -69%', 'DECELERATING earnings',
               'Composite TRIM'],
        tail=[],
        head=['Severe valuation premium', 'Composite TRIM'],
        stop_pct=-0.04, target_pct=0.03,
    ),
    'SEARL': dict(
        direction='BEARISH', conviction='HIGH',
        ret=(-3.0, -1.5, 0.0), action='AVOID',
        rationale=(
            'SELL_VALUE -85% (price 85% above fair value). LOW quality. '
            'Phase-1 mom -25%. Pred -3.4%. No catalyst. Avoid.'
        ),
        drivers=[],
        risks=['Severe overvaluation', 'LOW quality', 'Phase-1 mom -25%'],
        tail=[],
        head=['Severe overvaluation', 'LOW quality'],
        stop_pct=-0.04, target_pct=0.02,
    ),
    'TRG': dict(
        direction='BEARISH', conviction='HIGH',
        ret=(-3.0, -1.6, 0.0), action='AVOID',
        rationale=(
            'JUNK quality. Phase-1 mom -30% (worst tier). EROSION earnings. '
            'Earnings calendar: results May 14 (3 days). Pre-print blackout '
            'risk. AVOID.'
        ),
        drivers=[],
        risks=['JUNK quality', 'Phase-1 mom -30%',
               'Earnings May 14 — blackout window'],
        tail=[],
        head=['Quality', 'Phase-1', 'Earnings blackout'],
        stop_pct=-0.04, target_pct=0.02,
    ),
    'SYS': dict(
        direction='NEUTRAL', conviction='LOW',
        ret=(-1.0, 0.0, 1.0), action='HOLD',
        rationale=(
            'RECOVERING earnings but SELL_VALUE -56% (extreme tech-multiple '
            'premium). Phase-1 mom 0%. Wait for either fundamentals to catch '
            'up or price to mean-revert.'
        ),
        drivers=['RECOVERING earnings'],
        risks=['SELL_VALUE -56%', 'Phase-1 flat'],
        tail=['Earnings recovery'],
        head=['Tech multiple premium'],
        stop_pct=-0.05, target_pct=0.05,
    ),
    'PABC': dict(
        direction='BEARISH', conviction='HIGH',
        ret=(-3.5, -2.0, -0.5), action='AVOID',
        rationale=(
            'Phase-1 worst in universe (mom -40%). EROSION earnings. Pred '
            '-3.9%. No clear catalyst. AVOID.'
        ),
        drivers=[],
        risks=['Phase-1 worst in universe', 'EROSION earnings'],
        tail=[],
        head=['Phase-1 -40%', 'Earnings erosion'],
        stop_pct=-0.04, target_pct=0.02,
    ),
}

# Sanity: every universe stock covered
universe = list(per_stock.keys())
missing = [s for s in universe if s not in P]
extra = [s for s in P if s not in per_stock]
if missing:
    print(f'WARNING: missing predictions for: {missing}')
if extra:
    print(f'WARNING: extra predictions not in universe: {extra}')

# ---------------------------------------------------------------------------
# Build prediction records
# ---------------------------------------------------------------------------
records: list[dict] = []
ohlcv_fallback_used: list[str] = []
for sym in universe:
    if sym not in P:
        continue
    pd_data = per_stock[sym]
    p = P[sym]
    entry = float(pd_data.get('close_pkr') or 0)
    if entry <= 0:
        fallback = _last_close_from_ohlcv(sym, asof=pd_data.get('as_of_price_date'))
        if fallback and fallback > 0:
            entry = float(fallback)
            ohlcv_fallback_used.append(sym)
    ret_low, ret_mid, ret_high = p['ret']
    stop = round(entry * (1.0 + p['stop_pct']), 2) if entry > 0 else None
    target = round(entry * (1.0 + p['target_pct']), 2) if entry > 0 else None

    # Macro impact mini-snapshot
    mi = {
        'score': pd_data.get('mi_score', 0),
        'tailwinds': pd_data.get('mi_tailwinds') or [],
        'headwinds': pd_data.get('mi_headwinds') or [],
    }

    rec = {
        'prediction_id': f'{TODAY}-{sym}',
        'generated_at': NOW,
        'symbol': sym,
        'sector': pd_data.get('sector', '?'),
        'model': MODEL,
        'horizon_trading_days': HORIZON,
        'entry_price_pkr': entry,
        'direction': p['direction'],
        'conviction': p['conviction'],
        'expected_return_5d_low_pct': ret_low,
        'expected_return_5d_mid_pct': ret_mid,
        'expected_return_5d_high_pct': ret_high,
        'suggested_action': p['action'],
        'suggested_stop_pkr': stop,
        'suggested_target_pkr': target,
        'rationale': p['rationale'],
        'key_drivers': p['drivers'],
        'key_risks': p['risks'],
        'macro_tailwinds': p['tail'],
        'macro_headwinds': p['head'],
        'macro_impact': mi,
        'mpc_alert': None,
        'mpc_cap_applied': False,
        'data_snapshot': {
            'as_of_price_date': '2026-05-08',
            'close_pkr': entry,
            'phase1_score': pd_data.get('p1_score'),
            'phase1_rank': pd_data.get('p1_rank'),
            'phase1_in_top5': bool(sym in ['NPL', 'ATRL', 'OGDC', 'KEL', 'PPL']),
            'phase1_in_top5_if_filter_off': bool(sym in
                ['ATRL', 'OGDC', 'KEL', 'EPCL', 'MCB']),
            'val_signal': pd_data.get('val_signal'),
            'val_upside_pct': pd_data.get('val_upside_pct'),
            'qual_band': pd_data.get('qual_band'),
            'qual_score': pd_data.get('qual_score'),
            'em_flag': pd_data.get('em_flag'),
            'composite_verdict': pd_data.get('verdict_action'),
            'composite_conviction': pd_data.get('verdict_conviction'),
            'volume_breakout_3d': pd_data.get('vol_breakout_3d'),
            'mf_change_30d_pct': pd_data.get('mf_change_30d_pct'),
            'mf_data_age_days': pd_data.get('mf_data_age_days'),
            'next_earnings_days': pd_data.get('next_earnings_days'),
            'in_blackout': pd_data.get('in_blackout'),
        },
    }
    records.append(rec)

print(f'\nGenerated {len(records)} predictions')

# ---------------------------------------------------------------------------
# Merge into existing predictions_log.json (overwriting today's IDs)
# ---------------------------------------------------------------------------
log: dict = {'version': 1, 'predictions': []}
if LOG_PATH.exists():
    log = json.loads(LOG_PATH.read_text(encoding='utf-8'))
preds = log.get('predictions') or []

# Index existing by prediction_id (keep most recent generated_at if duplicate)
by_id: dict[str, dict] = {}
for p in preds:
    pid = p.get('prediction_id')
    if not pid:
        continue
    by_id[pid] = p

# Apply new
for rec in records:
    by_id[rec['prediction_id']] = rec

merged = list(by_id.values())
log['predictions'] = merged
log['version'] = 1

LOG_PATH.write_text(
    json.dumps(log, indent=2, default=str, ensure_ascii=False),
    encoding='utf-8'
)
print(f'\nMerged into {LOG_PATH}')
print(f'Total predictions in log: {len(merged)}')

if ohlcv_fallback_used:
    print(f'OHLCV fallback used for: {ohlcv_fallback_used}')

# -- Self-heal: ensure no row in today's batch ships with entry=0 / stop=None /
#    target=None. The patcher is a defense-in-depth net even when the writer
#    succeeds, so future schema regressions are caught at source.
try:
    from scripts._patch_pred_prices import backfill_prices, check_only
    summary = backfill_prices(latest_only=True, dry_run=False, verbose=True)
    if summary['patched_count']:
        print(
            f'[self-heal] backfilled prices for: {summary["patched"]}'
        )
    rc = check_only(latest_only=True)
    if rc != 0:
        print('[self-heal] WARNING: check_only failed after backfill — '
              'some rows in today\'s batch still missing prices.')
except Exception as exc:
    print(f'[self-heal] could not run patcher: {exc!r}')

# Show distribution
counts_dir: dict[str, int] = {}
counts_act: dict[str, int] = {}
for r in records:
    counts_dir[r['direction']] = counts_dir.get(r['direction'], 0) + 1
    counts_act[r['suggested_action']] = counts_act.get(r['suggested_action'], 0) + 1
print(f'\nDirection distribution: {counts_dir}')
print(f'Action distribution: {counts_act}')

# Sanity: avg expected return
avg_mid = sum(r['expected_return_5d_mid_pct'] for r in records) / len(records)
avg_abs = sum(abs(r['expected_return_5d_mid_pct']) for r in records) / len(records)
print(f'Avg expected mid 5d: {avg_mid:+.2f}% (abs {avg_abs:.2f}%)')
