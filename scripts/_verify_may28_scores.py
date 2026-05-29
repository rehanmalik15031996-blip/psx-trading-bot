import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from _scores_may28 import MAY28_SCORES

arts = json.loads(Path("data/news/_pending_articles.json").read_text(encoding="utf-8"))
ids = {a["article_id"] for a in arts}
missing = ids - set(MAY28_SCORES.keys())
print(f"Pending: {len(ids)}, Scored: {len(MAY28_SCORES)}, Missing: {len(missing)}")
for m in sorted(missing):
    a = next(x for x in arts if x["article_id"] == m)
    print(f"  {m} | {(a.get('title') or '')[:70]}")
high = [k for k, v in MAY28_SCORES.items() if v[1] == "HIGH"]
print(f"HIGH count: {len(high)}")
