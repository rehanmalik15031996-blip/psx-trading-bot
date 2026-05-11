"""Quick: show entry/stop/target for the latest prediction per symbol."""
import json, pathlib

log = json.loads(pathlib.Path("data/predictions_log.json").read_text(encoding="utf-8"))
preds = log["predictions"]

SYMS = ("OGDC","POL","ATRL","PPL","MARI","NBP","MCB","FATIMA","HUBC","NPL","EPCL","FFC","PABC")
for sym in SYMS:
    rows = [r for r in preds if r.get("symbol") == sym]
    rows.sort(key=lambda r: r.get("generated_at") or "")
    if not rows:
        print(f"{sym:<8} : NO ROWS")
        continue
    r = rows[-1]
    gen = (r.get("generated_at") or "")[:10]
    mdl = (r.get("model") or "")[:35]
    entry = r.get("entry_price_pkr")
    stop = r.get("suggested_stop_pkr")
    tgt = r.get("suggested_target_pkr")
    close = (r.get("data_snapshot") or {}).get("close_pkr")
    act = r.get("suggested_action")
    print(f"{sym:<8} gen={gen} | act={act:<6} | entry={entry} stop={stop} tgt={tgt} | close_snap={close}")
