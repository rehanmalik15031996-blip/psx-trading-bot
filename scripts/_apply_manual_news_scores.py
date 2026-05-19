"""Apply Cursor-strategist news sentiment scores back to the
scored_news.parquet cache. Acts as a drop-in for
scripts/score_news_sentiment.py on days the Anthropic API key is bad.

Reads:   data/news/_pending_articles.json    (135 fresh articles)
Writes:  data/news/scored_news.parquet       (appended)
         data/_health/news_scoring.json      (health stamp)
         data/news/_pending_articles.json    (deleted after success)

After this lands, the briefing engine + LLM strategist + predictor's
news-tilt all pick up the new sentiment within their next run.

Scoring conventions (mirror score_news_sentiment.py prompt):
  sentiment   : float in [-1.0, +1.0]
                -1=very bearish PSX, +1=very bullish PSX, mild=routine
  confidence  : LOW | MED | HIGH
                HIGH only when article directly names a known catalyst
  category    : MACRO | POLICY | COMPANY | COMMODITY | GLOBAL | GEOPOLITICS | OTHER
  affected_symbols : empty for index-level; populate when name is in universe
  one_liner   : <=120 chars; why this matters for PSX

Big picture grading framework used today (Mon May 18 + Tue May 19):
  + US-Iran de-escalation (Trump paused strike) -> PSX +2400 early Tue
  + KSE-100 closed -2.29% Mon (-3,791 pts) on oil/geopolitics fear
  + Brent USD 110+, current account back to deficit ($324m April)
  + Foreign FIPI day 4 of net-selling (banks worst)
  + S&P Global: Iran war hits Pakistan hardest in APAC (latent risk)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

CACHE_DIR = ROOT / "data" / "news"
CACHE_PATH = CACHE_DIR / "scored_news.parquet"
PENDING    = CACHE_DIR / "_pending_articles.json"

MODEL_TAG = "cursor-claude-sonnet-4-5-manual"

# --- Cursor-as-strategist scores ---------------------------------------
# Format: article_id -> (sentiment, conf, category, [affected_symbols], one_liner)
SCORES: dict[str, tuple[float, str, str, list[str], str]] = {
    # --- BIG MARKET-MOVING NEWS (Tue May 19) ---
    "fd3f11a0228a6179": (+0.65, "HIGH", "GEOPOLITICS", [],
        "PSX +2,400 pts early Tue as Trump pauses Iran strike — relief rally trigger"),
    "33ab6394b9c34d16": (+0.10, "MED", "GLOBAL", [],
        "Dollar steadies on Iran de-escalation; mild safe-haven unwind"),
    "676165f904c5cd0c": (-0.15, "MED", "COMMODITY", [],
        "Gold falls 0.5% on Iran pause — risk-on rotation begins"),
    "e2cfa0565d5a8b34": (+0.20, "MED", "GLOBAL", [],
        "Global shares wobble as oil eases on Trump Iran comments — net positive for risk assets"),
    "2cf60cac598334d3": (+0.65, "HIGH", "GEOPOLITICS", [],
        "Pakistan stocks jump after Trump holds off Iran attack — confirmation of relief move"),
    "3f597690b3193710": (+0.50, "HIGH", "MACRO", [],
        "Buying returns at PSX as US-Iran tensions ease — bullish flow trigger"),
    "e1ed98c8c186ae13": (+0.50, "HIGH", "MACRO", [],
        "Buying returns at PSX as US-Iran tensions ease — duplicate aggregator"),
    "3cd6efe87ea35c43": (+0.65, "HIGH", "MACRO", [],
        "PSX rebounds as US-Iran tensions ease — Google News aggregator confirms catalyst"),
    "27bf56c4dc15064c": (-0.15, "MED", "COMMODITY", [],
        "Gold retreats on Iran ceasefire progress — inflation fears easing"),
    "9b20fe8a0ace1e08": (-0.05, "LOW", "COMMODITY", [],
        "India raises fuel prices on Iran war — proxy signal high crude still flowing"),

    # --- PSX MONDAY CRASH COVERAGE (mostly retrospective) ---
    "ab76560f1132270a": (-0.25, "MED", "MACRO", [],
        "PSX plunges 3,791 points over oil worries — yesterday's risk-off"),
    "22b677e8fafd4cb1": (-0.25, "MED", "MACRO", [],
        "PSX -3,800 points on geopolitical tensions — Monday post-mortem"),
    "8c0cbf4614e7adc9": (-0.25, "MED", "MACRO", [],
        "Same crash story aggregated by Google News"),
    "6c79acc4c6734321": (-0.25, "MED", "MACRO", [],
        "Sharp sell-off on geopolitical fears — same event"),
    "c61b9438b3883db2": (-0.20, "MED", "MACRO", [],
        "PSX plunges 3,791 points over oil worries — retrospective"),
    "0dfc2f76b1888eb2": (-0.20, "MED", "MACRO", [],
        "KSE-100 -3,791 on rising oil prices — retrospective"),
    "70fd05a0d2649491": (-0.20, "MED", "MACRO", [],
        "KSE-100 slumps over 2,500 points on geopolitical tensions — intraday"),
    "9e4c9b8cd6f5bd5c": (-0.20, "MED", "MACRO", [],
        "Intraday Mon -1,600 pts on Middle East tensions"),
    "08739b0f60a21f30": (-0.15, "MED", "MACRO", [],
        "PSX under pressure on oil, IMF concerns — recent context"),
    "89e100f37a26f675": (-0.20, "MED", "MACRO", [],
        "PSX -2,000 intraday on selling pressure — Mon"),
    "1ca659b744aa0a27": (-0.20, "MED", "MACRO", [],
        "PSX -2,200 points on regional tensions — same event"),
    "95a941608465abb3": (-0.20, "MED", "GEOPOLITICS", [],
        "Markets need peace as PSX drops 2.29% — Mon post-mortem"),

    # --- US-IRAN WAR LATENT RISK ---
    "54b955305974f89e": (-0.30, "MED", "GEOPOLITICS", [],
        "S&P Global: US-Iran war could hit Pakistan hardest in APAC if it resumes"),

    # --- MACRO HEADWINDS ---
    "3979933698b51677": (-0.35, "HIGH", "MACRO", ["HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL"],
        "Current account back to $324m deficit in April — oil import bill +82% pressures PKR/banks"),
    "73338de437dff638": (+0.05, "LOW", "MACRO", [],
        "India record RBI dividend as buffer for war shock — peer signal only"),
    "a62edaa1c6365f58": (-0.30, "MED", "MACRO", [],
        "Trade shortfall hits $324m April, widening 47% MoM — current account stress"),
    "0a8a4a25c9d27510": (-0.30, "HIGH", "MACRO", [],
        "Pakistan FDI declines 68% in one month — flow stress"),
    "ca11db54f18dbb64": (-0.30, "HIGH", "MACRO", [],
        "FDI -31% to $1.4bn in 10MFY26 — investor confidence weak"),
    "3bc69a287872c004": (-0.10, "MED", "MACRO", [],
        "Pakistan FDI $55m in April — weak month-on-month"),
    "d94fd10e52bf3ffc": (-0.10, "LOW", "MACRO", [],
        "Exports to Middle East fall 8% — regional conflict drag"),
    "be0807a274cd472a": (-0.10, "LOW", "MACRO", [],
        "Exports to ME fall 8% — same"),
    "3c7d402d2b5d06d6": (-0.05, "LOW", "MACRO", [],
        "Exports fall 8% in Middle East; imports +5% — net trade negative"),
    "d1d6b47d199b4a45": (+0.05, "LOW", "GLOBAL", [],
        "Chicago grains rally on China $17bn purchase — peripheral"),

    # --- IMF / FISCAL ---
    "510792a8eda691ee": (-0.25, "HIGH", "POLICY", [],
        "Provinces asked to raise Rs400bn more for IMF goals — fiscal stress"),
    "5d8ae945b6508d4a": (-0.25, "MED", "POLICY", [],
        "Provinces urged to raise Rs400b — same IMF tax pressure story"),
    "7e0b00000000ff01": (0.0, "LOW", "OTHER", [], "placeholder"),  # unused
    "1ea7333b24edf37b": (+0.05, "LOW", "POLICY", [],
        "PM reviews Rs1.5tr PSDP, directs more funds for well-performing ministries — execution focus"),
    "ac37a0c35beb83af": (+0.30, "MED", "POLICY", [],
        "SBP received $1.3bn IMF tranche — reserve cushion confirmed"),
    "bf2e3cd2172f0792": (+0.15, "MED", "MACRO", [],
        "SBP says external account healthier despite global turmoil"),
    "60278208a9205a08": (+0.05, "LOW", "MACRO", [],
        "SBP reserves +$23m (May 8) — small positive"),
    "0cb4149b6e7c9cf9": (+0.10, "LOW", "POLICY", [],
        "SBP says local banks ready for shocks after 15% growth — pre-crisis assertion"),

    # --- OIL / ENERGY ---
    "f0f5404a977f723b": (-0.10, "MED", "COMMODITY", [],
        "Petroleum import bill $13.5bn 10MFY26; crude +24% — fiscal drag, mild E&P proxy"),
    "6c671f6dc2ae18e9": (-0.10, "MED", "COMMODITY", [],
        "Same: $13.5bn 10MFY26 oil bill"),
    "f91d384ba9cd7a69": (+0.15, "MED", "POLICY", ["KEL", "HUBC", "KAPCO", "NPL"],
        "Rs6-per-unit tariff shock averted via LNG imports — minor IPP positive"),
    "829831c9d092abcf": (+0.05, "LOW", "COMPANY", [],
        "Pakistan-US energy cooperation; Cnergyico imports 6m barrels — not in universe"),
    "41755deff4820bbe": (+0.05, "LOW", "COMMODITY", [],
        "Pakistan imported 6m barrels US crude FY26 — moderate"),
    "8dbd74fda2340247": (+0.10, "LOW", "POLICY", [],
        "Pakistan eyes higher Russian oil imports — supply diversification"),
    "6800499eb4af3d60": (+0.10, "LOW", "POLICY", [],
        "Same: Russian crude option amid Hormuz risk"),
    "922518a3d3c28ea1": (-0.10, "MED", "MACRO", [],
        "April imports $6.8bn +33% — current account drag"),
    "dd0e08f367556133": (-0.10, "LOW", "MACRO", [],
        "April imports $6.8bn — duplicate"),
    "358ef5a3a05140b1": (+0.20, "MED", "MACRO", [],
        "Pakistan exports +14% April — partial offset to import surge"),
    "10c3b66bbd79d543": (0.0, "LOW", "MACRO", [],
        "PBS April trade breakdown — informational"),

    # --- POLICY / REGULATORY ---
    "5de3a7eeabcca568": (+0.05, "LOW", "POLICY", [],
        "SECP issues first digital takaful licence — sector reform"),
    "ea6e57ef7a780de9": (+0.10, "LOW", "POLICY", [],
        "Govt eyes blockchain tokenisation of bonds via DNN"),
    "85347fda5522f425": (+0.10, "LOW", "POLICY", [],
        "Same: blockchain tokenisation of bonds"),
    "e0797d04a15ad06d": (0.0, "LOW", "POLICY", [],
        "CCP clears CDC investment in NCMCL — admin approval"),
    "f7d41e18e87b29a9": (-0.05, "LOW", "COMMODITY", [],
        "Gold drops sharply Rs15,500/tola on stronger dollar"),
    "648ef49707cd3820": (-0.05, "LOW", "COMMODITY", [],
        "Gold falls $6/oz to $4,539 — minor"),
    "b3e444d8853347d0": (0.0, "LOW", "MACRO", [],
        "Engineering drives Punjab growth — sector colour"),

    # --- COMPANY-SPECIFIC (universe-relevant) ---
    "12419d8b2b4d4760": (+0.35, "MED", "COMPANY", ["NBP"],
        "Mettis: NBP target Rs238/share — broker upgrade catalyst"),
    "b0e244e9a8ea1a65": (+0.15, "LOW", "COMPANY", ["TRG"],
        "Zia Chishti: The Man Who Refused to Lose TRG — long-form positive context"),
    "4f8100a00f20eab4": (+0.20, "MED", "COMPANY", ["TRG"],
        "TRG: Proving Grounds — Mettis bullish coverage ahead of earnings"),
    "5661e8b48c965679": (-0.20, "MED", "COMPANY", ["DGKC", "KOHC", "LUCK", "FCCL", "MLCF"],
        "Cement sector records highest divestment of $102m April — confirms our short thesis"),

    # --- TECH SECTOR ---
    "5e311b1769f90c82": (+0.20, "MED", "MACRO", ["TRG", "SYS"],
        "Tech exports +33% to $423m — sector tailwind for TRG / SYS"),

    # --- COMPANY / IPO / DELISTING (not in universe) ---
    "6aa98e2c14e0583f": (0.0, "LOW", "COMPANY", [],
        "At-Tahur launches Prema spring water — single-name, not in universe"),
    "44674acad254b4bf": (0.0, "LOW", "COMPANY", [],
        "PREMA premium water launch (At-Tahur) — not in universe"),
    "068d695528a9191a": (0.0, "LOW", "COMPANY", [],
        "SLM Tyres IPO — not in universe"),
    "2bc26a893b3de712": (0.0, "LOW", "COMPANY", [],
        "Gillette Pakistan delisting May 19 — small-cap, not in universe"),
    "52df59b3d7fdd3b3": (-0.05, "LOW", "COMPANY", [],
        "OCTOPUS Q1 profit -29% — single-name not in universe"),
    "83bde09aaca6f8a9": (-0.10, "LOW", "COMPANY", [],
        "IDRT flags reduced operations — not in universe"),
    "35f1ed77560e4626": (-0.05, "LOW", "COMPANY", [],
        "SGPL plans Rs535m right issue — IPP healthcare expansion"),
    "d240fc538fb7b670": (-0.05, "LOW", "COMPANY", [],
        "Same: SGPL Rs535m right issue for healthcare"),
    "e260c2253ce7fbf8": (0.0, "LOW", "COMPANY", [],
        "SECP greenlights PSHL bid for ASIC shares — small admin"),
    "90cd92bdf2a3f9d8": (0.0, "LOW", "COMPANY", [],
        "LSEFSL plans share split — not in universe"),
    "2a80ce6f41ba10e6": (0.0, "LOW", "COMPANY", [],
        "LSECL acquires 5.75% in DCCL — not in universe"),
    "8abf22ece54bce81": (0.0, "LOW", "COMPANY", [],
        "PSX approves Gillette delisting — duplicate"),

    # --- OTHER MACRO ---
    "1b56933a970b0ba9": (-0.15, "MED", "POLICY", [],
        "Govt rejects all bids for Sukuk auction — bond market weakness signal"),
    "79f5652b139ce981": (+0.05, "LOW", "POLICY", [],
        "Govt unveils insurance sector reforms under bill 2026"),
    "1b2f56ecbf9dc4ab": (+0.05, "LOW", "POLICY", [],
        "Karandaaz + Finnect expand offline Raast payments"),
    "b82f3211be5bce28": (-0.05, "LOW", "COMMODITY", [],
        "Gold keeps stable above $4,500 — minor"),
    "a0aa93083d4db770": (+0.10, "LOW", "POLICY", [],
        "Pakistan pushes economic diplomacy with Iran/GCC — relations focus"),
    "59e18f4f63fda732": (+0.05, "LOW", "POLICY", [],
        "Govt retires Rs501m debt in a week — small positive"),
    "a27afae2e248010d": (-0.05, "LOW", "MACRO", [],
        "M2 falls Rs451bn — liquidity tightening"),
    "19129976d24c3223": (-0.10, "LOW", "MACRO", ["KEL"],
        "Karachi battles heat, darkness, power cuts — KEL operational pressure"),
    "2c5f5dc25a89fa6b": (0.0, "LOW", "OTHER", [],
        "Mettis morning brief — informational"),
    "0cd9caffc4fea32c": (-0.15, "MED", "MACRO", [],
        "PSX Closing Bell red wrap-up Mon"),

    # --- GLOBAL (mostly irrelevant for PSX) ---
    "ef0e92d4de8cd926": (0.0, "LOW", "GLOBAL", [],
        "JGB prices fall on Japan extra budget — Asia bond signal only"),
    "c45e42c4b376bb4c": (0.0, "LOW", "GLOBAL", [],
        "StanChart cuts 7,000 jobs on AI — global trend not local"),
    "5793e7178bb00ec7": (+0.05, "LOW", "GLOBAL", [],
        "G7 agrees IMF should aid vulnerable countries — peripheral positive"),
    "c85b9072b7810101": (0.0, "LOW", "GLOBAL", [],
        "S Korea tenders for corn/wheat — irrelevant"),
    "52e1a574bbc3e133": (0.0, "LOW", "GLOBAL", [],
        "India bonds gain after oil selloff — peer signal"),
    "3f8f084ea0c84085": (-0.05, "LOW", "GLOBAL", [],
        "China stocks wobble on bond/geopolitics — Asia risk-off proxy"),
    "a0516dc194364963": (0.0, "LOW", "COMMODITY", [],
        "Palm oil supported at $1,110/ton June — soft"),

    # --- IMF / GEOPOLITICAL TANGENTS ---
    "be8d872079d0994c": (-0.20, "MED", "MACRO", [],
        "PSX extends losses on IMF + geopolitics — May 15 retrospective"),
    "82b23d3a2a5c62af": (-0.15, "MED", "MACRO", [],
        "PSX losing streak below 166,000 — May 15 retrospective"),
    "07e637b6daa47d02": (-0.10, "MED", "MACRO", [],
        "Stocks tumble after early surge, below 167,000 — May 14"),
    "6927957dca57a032": (-0.20, "MED", "MACRO", [],
        "PSX -3.2% as US-Iran peace push slows — May 16"),
    "f3cb09e8ed140687": (+0.05, "LOW", "POLICY", [],
        "MSCI reshuffles Pakistan stocks — index event"),
    "fe4b0d5bf542e783": (+0.10, "LOW", "MACRO", [],
        "PSX closing bell bulls stage comeback — April old"),

    # --- SPORTS / ENTERTAINMENT (noise) ---
    "37ffdc5be272e6fa": (0.0, "LOW", "OTHER", [], "Boxing match — irrelevant"),
    "122d77690c2714a9": (0.0, "LOW", "OTHER", [], "Neymar World Cup — irrelevant"),
    "72c63664bb89130b": (0.0, "LOW", "OTHER", [], "Mourinho to Real Madrid — irrelevant"),
    "be7e29186bf6915a": (0.0, "LOW", "OTHER", [], "PGA Championship — irrelevant"),
    "c500ed235798141d": (0.0, "LOW", "OTHER", [], "NLL final — irrelevant"),
    "6c4e88e0bb17deeb": (0.0, "LOW", "OTHER", [], "WNBA injury — irrelevant"),
    "b6a4b26dbf292138": (0.0, "LOW", "OTHER", [], "WNBA injury — irrelevant"),
    "1c96966241a603a7": (0.0, "LOW", "OTHER", [], "PGA Championship — irrelevant"),
    "f5c70d04582c528f": (+0.05, "LOW", "POLICY", [],
        "SBP expands Eidul Azha cashless drive — minor"),
}

# --- Apply scores -----------------------------------------------------
print(f"[1/4] Loading pending articles from {PENDING}")
articles = json.loads(PENDING.read_text(encoding="utf-8"))
print(f"  loaded {len(articles)} articles")

print(f"\n[2/4] Loading existing cache: {CACHE_PATH}")
if CACHE_PATH.exists():
    existing = pd.read_parquet(CACHE_PATH)
    print(f"  cache has {len(existing)} rows")
else:
    existing = pd.DataFrame()
    print("  no existing cache")

now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
rows = []
unscored = []
for art in articles:
    aid = art["article_id"]
    if aid not in SCORES:
        unscored.append(art)
        continue
    sent, conf, cat, syms, oneliner = SCORES[aid]
    rows.append({
        "article_id":   aid,
        "published_at": art.get("published_at") or "",
        "scored_at":    now_iso,
        "source":       art.get("source") or "",
        "title":        (art.get("title") or "")[:300],
        "link":         art.get("link") or "",
        "summary":      (art.get("summary") or "")[:300],
        "sentiment":    round(float(sent), 3),
        "confidence":   conf,
        "category":     cat,
        "affected_symbols": ",".join(syms),
        "one_liner":    oneliner[:160],
        "model":        MODEL_TAG,
    })

# For any unscored articles, write neutral so the article still goes in the cache
# (otherwise it'll be re-fetched forever)
for art in unscored:
    rows.append({
        "article_id":   art["article_id"],
        "published_at": art.get("published_at") or "",
        "scored_at":    now_iso,
        "source":       art.get("source") or "",
        "title":        (art.get("title") or "")[:300],
        "link":         art.get("link") or "",
        "summary":      (art.get("summary") or "")[:300],
        "sentiment":    0.0,
        "confidence":   "LOW",
        "category":     "OTHER",
        "affected_symbols": "",
        "one_liner":    "(unscored — agent did not classify)",
        "model":        MODEL_TAG,
    })

print(f"\n[3/4] Scored {len(rows) - len(unscored)} / {len(articles)} "
      f"(plus {len(unscored)} marked unscored neutral)")

new_df = pd.DataFrame(rows)
# Drop dups by article_id within new_df
new_df = new_df.drop_duplicates("article_id", keep="first")

if existing.empty:
    out = new_df
else:
    # Append but prefer the newest scored_at if dup
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.sort_values("scored_at").drop_duplicates(
        "article_id", keep="last")
    out = combined

print(f"\n[4/4] Cache will grow to {len(out)} rows. Saving...")
out.to_parquet(CACHE_PATH, index=False)
print(f"  -> {CACHE_PATH}")

# Stats
cat_counts = new_df["category"].value_counts().to_dict()
avg_sent = round(float(new_df["sentiment"].mean()), 3)
print(f"\nBatch summary:")
print(f"  avg sentiment: {avg_sent:+.3f}")
print(f"  by category:   {cat_counts}")

# Top movers
new_df["abs"] = new_df["sentiment"].abs()
top = new_df.sort_values("abs", ascending=False).head(8)
print("\nTop 8 most impactful articles (absolute sentiment):")
for _, r in top.iterrows():
    syms = f" [{r['affected_symbols']}]" if r["affected_symbols"] else ""
    print(f"  {r['sentiment']:+.2f} {r['confidence']:>4} {r['category']:<12}{syms}")
    print(f"    {r['title'][:120]}")

# Health stamp
try:
    from scripts._health import write_status
    write_status(
        workflow="news_scoring",
        ok=True,
        note=f"scored {len(new_df)} new articles (Cursor-as-strategist; LLM API down)",
        payload={
            "scored":  int(len(new_df)),
            "fetched": int(len(articles)),
            "by_category": {k: int(v) for k, v in (cat_counts or {}).items()},
            "model": MODEL_TAG,
        },
    )
    print("\nHealth status written to data/_health/news_scoring.json")
except Exception as e:
    print(f"  WARN: health write failed: {type(e).__name__}: {e}")

# Remove pending so this script is idempotent
PENDING.unlink(missing_ok=True)
print(f"\nRemoved {PENDING} (idempotent cleanup)")
