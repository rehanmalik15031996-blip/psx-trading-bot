"""Decode AHL ObjectId hex strings to publication dates."""
import sys
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding='utf-8')

candidates = [
    "69174b26df961712509a9c38",  # web search hit
    "697cc96f2c2a6a5d49218760",  # web search hit
    "69a17f87d33a93d06bd96d15",  # web search hit
    "69cfcb1eb56b444d53036259",  # web search hit
]

for h in candidates:
    ts = int(h[:8], 16)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    print(f"{h}  pub_date={dt.date().isoformat()}  ({dt.isoformat()})")
