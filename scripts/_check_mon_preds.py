"""Did Monday's predictions warn about the names that bled?"""
import json, pathlib

preds = json.loads(pathlib.Path("data/predictions_log.json").read_text(encoding="utf-8"))["predictions"]
mon = [p for p in preds if isinstance(p, dict) and p.get("prediction_id", "").startswith("2026-05-11")]
print(f"Monday predictions: {len(mon)}")
worst = ["HBL", "KEL", "KOHC", "NPL", "SEARL", "BAHL", "NBP", "DGKC", "MLCF", "FCCL"]
print(f"\n{'sym':<8}{'direction':<12}{'action':<8}{'conf':<10}{'ret_band [low/mid/high]':<28}{'2d_actual_%'}")
moves = {"HBL":-5.34,"KEL":-4.92,"KOHC":-4.90,"NPL":-4.06,"SEARL":-3.90,
         "BAHL":-3.88,"NBP":-3.81,"DGKC":-3.50,"MLCF":-3.39,"FCCL":-3.29}
for sym in worst:
    m = next((p for p in mon if p.get("symbol") == sym), None)
    if not m: continue
    band = f"[{m.get('expected_return_low_pct','?')}/{m.get('expected_return_mid_pct','?')}/{m.get('expected_return_high_pct','?')}]"
    print(f"  {sym:<6}{m.get('direction','?'):<12}{m.get('suggested_action','?'):<8}"
          f"{m.get('confidence','?'):<10}{band:<28}{moves.get(sym,'?')}")
