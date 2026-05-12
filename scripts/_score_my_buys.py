"""Score a hypothetical buy vs the strategist model levels for any symbol."""
import json, pathlib
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG = ROOT / "data/predictions_log.json"

LIVE = {
    "OGDC": {"last": 326.00, "open": 324.80, "low": 321.00, "high": 327.50, "ldcp": 326.43, "vol": 2793256},
    "POL":  {"last": 659.85, "open": 657.10, "low": 654.00, "high": 660.95, "ldcp": 657.58, "vol": 56356},
    "PPL":  {"last": 230.13, "open": 228.95, "low": 227.00, "high": 231.60, "ldcp": 229.93, "vol": 2284035},
    "ATRL": {"last": 903.00, "open": 891.10, "low": 891.10, "high": 914.00, "ldcp": 903.72, "vol": 185068},
}


def find_pred(sym: str) -> Optional[dict]:
    log = json.loads(LOG.read_text(encoding="utf-8"))
    rows = [r for r in log.get("predictions", []) if r.get("symbol") == sym]
    rows.sort(key=lambda r: r.get("generated_at") or "")
    return rows[-1] if rows else None


def score(sym: str, my_entry: Optional[float] = None) -> str:
    p = find_pred(sym)
    if not p:
        return f"{sym}: NO PREDICTION"
    live = LIVE.get(sym, {})
    last = live.get("last")
    entry = p.get("entry_price_pkr")
    stop = p.get("suggested_stop_pkr")
    tgt = p.get("suggested_target_pkr")
    act = p.get("suggested_action")
    direc = p.get("direction")
    conv = p.get("conviction")
    rl, rm, rh = (
        p.get("expected_return_5d_low_pct"),
        p.get("expected_return_5d_mid_pct"),
        p.get("expected_return_5d_high_pct"),
    )

    lines = []
    lines.append(f"=== {sym} === {act}/{direc}/{conv}")
    lines.append(f"  Model:    entry={entry}  stop={stop}  target={tgt}")
    lines.append(f"  Model%:   low={rl}  mid={rm}  high={rh}")
    lines.append(f"  Live:     last={last}  range={live.get('low')}-{live.get('high')}  vol={live.get('vol'):,}")
    if last and entry:
        diff = (last - entry) / entry * 100
        lines.append(f"  vs entry: {diff:+.2f}%  ({'BELOW' if diff < 0 else 'ABOVE'})")

    if my_entry:
        # Risk/reward from MY entry, using strategist stop/target
        if act in ("BUY", "ADD") and stop and tgt:
            risk = (my_entry - stop) / my_entry * 100
            reward = (tgt - my_entry) / my_entry * 100
            rr = reward / risk if risk > 0 else None
            lines.append(f"  MY ENTRY: {my_entry}")
            lines.append(f"    vs model entry: {((my_entry - entry)/entry*100):+.2f}%")
            lines.append(f"    risk to stop:   {risk:.2f}%  (stop @ {stop})")
            lines.append(f"    reward to tgt:  {reward:.2f}%  (tgt @ {tgt})")
            lines.append(f"    R:R:            1:{rr:.2f}" if rr else "    R:R: undefined")
            # P&L vs current live
            pnl = (last - my_entry) / my_entry * 100
            lines.append(f"    LIVE P&L:       {pnl:+.2f}%  (last @ {last})")
        elif act in ("HOLD", "WATCH") and stop and tgt:
            # In a HOLD setup we use the model's stop/target as proxy levels
            risk = (my_entry - stop) / my_entry * 100
            reward = (tgt - my_entry) / my_entry * 100
            rr = reward / risk if risk > 0 else None
            lines.append(f"  MY ENTRY: {my_entry}  (model says HOLD, not BUY)")
            lines.append(f"    risk to stop:   {risk:.2f}%  (stop @ {stop})")
            lines.append(f"    reward to tgt:  {reward:.2f}%  (tgt @ {tgt})")
            lines.append(f"    R:R:            1:{rr:.2f}" if rr else "    R:R: undefined")
            pnl = (last - my_entry) / my_entry * 100
            lines.append(f"    LIVE P&L:       {pnl:+.2f}%  (last @ {last})")
    return "\n".join(lines)


if __name__ == "__main__":
    print(score("OGDC", my_entry=326.50))
    print()
    print(score("POL", my_entry=660.00))
    print()
    print(score("PPL", my_entry=230.20))
    print()
    print(score("ATRL", my_entry=903.00))
