"""PSX transaction cost model — what it really costs to round-trip a trade.

PSX has multiple fee layers on every side of a trade. The numbers below
reflect the 2025-26 PSX retail brokerage norm; adjust BROKERAGE_PCT
downward (~0.03%) if you're trading through a discount broker.

Round-trip cost per trade (buy + sell), as a percentage of notional:

    Brokerage               0.15% * 2 sides          = 0.30%
    CDC charges             0.005% * 2 sides         = 0.01%
    PSX laga                0.0015% * 2 sides        = 0.003%
    SECP fee                0.00005% * 2 sides       = 0.0001%
    FED on brokerage        16% of brokerage         = 0.048%
    Total excl. taxes                                ~ 0.36%
    CGT on gains (settlement-time, not round-trip)   : IGNORED here
                                                       (applied to P&L)
    -----------------------------------------------
    Round-trip all-in rough: ~0.40%

On top of the fee drag there's market-impact / slippage from crossing
the bid-ask spread. For mid-cap PSX blue chips this is 0.10-0.30% per
round-trip. We add 0.20% as a default assumption. Net:

    Total round-trip with slippage = ~0.60%

For a 5-day horizon expected return to be worth the trade:
    EXPECTED_GROSS_RETURN > ROUND_TRIP_COST + MINIMUM_EDGE
    i.e. gross >= 0.60% + 1.0% edge = 1.60%
"""

from __future__ import annotations

# --- Fee layers (%) on notional value per SIDE of the trade -----------------
BROKERAGE_PCT_PER_SIDE   = 0.15        # typical retail
CDC_PCT_PER_SIDE         = 0.005
PSX_LAGA_PCT_PER_SIDE    = 0.0015
SECP_FEE_PCT_PER_SIDE    = 0.00005
FED_ON_BROKERAGE_PCT     = 16.0        # 16% sales tax on brokerage

# --- Slippage (market impact) per round-trip -------------------------------
SLIPPAGE_PCT_ROUND_TRIP  = 0.20        # liquid blue chips

# --- Minimum net edge required to justify a trade --------------------------
MINIMUM_NET_EDGE_PCT     = 1.0         # must clear costs by at least 1 pp

# --- CGT on gains (applied ONLY to positive P&L, not to gross return) ------
CGT_ON_GAINS_PCT         = 15.0        # 2025-26 filer rate for securities held < 1y


def round_trip_cost_pct() -> float:
    """All-in round-trip cost % (excluding CGT which applies to gains only)."""
    brokerage = BROKERAGE_PCT_PER_SIDE * 2
    cdc       = CDC_PCT_PER_SIDE * 2
    laga      = PSX_LAGA_PCT_PER_SIDE * 2
    secp      = SECP_FEE_PCT_PER_SIDE * 2
    fed       = brokerage * (FED_ON_BROKERAGE_PCT / 100.0)
    fees      = brokerage + cdc + laga + secp + fed
    return round(fees + SLIPPAGE_PCT_ROUND_TRIP, 3)


def net_return_pct(gross_return_pct: float,
                   apply_cgt: bool = True) -> float:
    """Convert gross expected return to net after costs and (optionally) CGT.

    - Subtract round-trip cost always.
    - If gross > cost AND apply_cgt: apply CGT to the positive net portion.
    """
    cost = round_trip_cost_pct()
    pre_cgt = gross_return_pct - cost
    if apply_cgt and pre_cgt > 0:
        return round(pre_cgt * (1 - CGT_ON_GAINS_PCT / 100.0), 3)
    return round(pre_cgt, 3)


def minimum_gross_for_trade() -> float:
    """Lowest gross return % that still clears costs + minimum edge."""
    return round(round_trip_cost_pct() + MINIMUM_NET_EDGE_PCT, 3)


def trade_is_viable(gross_return_pct: float) -> tuple[bool, dict]:
    """Return (viable, diagnostic_dict)."""
    cost = round_trip_cost_pct()
    net = net_return_pct(gross_return_pct)
    threshold = minimum_gross_for_trade()
    return (gross_return_pct >= threshold), {
        "gross_return_pct": round(gross_return_pct, 3),
        "round_trip_cost_pct": cost,
        "net_after_costs_pct": round(gross_return_pct - cost, 3),
        "net_after_costs_and_cgt_pct": net,
        "minimum_gross_required_pct": threshold,
        "viable": gross_return_pct >= threshold,
    }


def describe_costs() -> str:
    return (
        f"PSX round-trip cost model:\n"
        f"  Brokerage     {BROKERAGE_PCT_PER_SIDE*2:.3f}% (both sides)\n"
        f"  CDC/PSX/SECP  {(CDC_PCT_PER_SIDE+PSX_LAGA_PCT_PER_SIDE+SECP_FEE_PCT_PER_SIDE)*2:.4f}%\n"
        f"  FED on brok.  {BROKERAGE_PCT_PER_SIDE*2*FED_ON_BROKERAGE_PCT/100.0:.3f}%\n"
        f"  Slippage      {SLIPPAGE_PCT_ROUND_TRIP:.3f}%\n"
        f"  -----------------------\n"
        f"  TOTAL         {round_trip_cost_pct():.3f}%  (round-trip, excl CGT)\n"
        f"  CGT on gains  {CGT_ON_GAINS_PCT:.1f}% (applied to net positive P&L only)\n"
        f"  Minimum gross for trade = cost + {MINIMUM_NET_EDGE_PCT:.2f}% edge "
        f"= {minimum_gross_for_trade():.2f}%"
    )


if __name__ == "__main__":
    print(describe_costs())
    print("\nExamples:")
    for g in [0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0]:
        ok, d = trade_is_viable(g)
        flag = "TAKE " if ok else "SKIP"
        print(f"  {flag} gross={g:>5.2f}%  -> net(cost+CGT)="
              f"{d['net_after_costs_and_cgt_pct']:>6.2f}%  "
              f"(threshold={d['minimum_gross_required_pct']:.2f}%)")
