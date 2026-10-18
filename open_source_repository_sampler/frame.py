"""The sampling frame: candidate repositories per stratum, and their seeded draw order.

A stratum is (GitHub language, popularity tier). GitHub search returns at most 1000 results
sorted by stars, so fetching a tier whole samples only its most-starred top. Each tier is
therefore split into narrow star bands; each band's candidates are shuffled with the seed, and
the draw order interleaves the bands at random in proportion to each band's GitHub match
count, so the draw follows the population across the tier rather than its top.

The shuffle for a stratum is seeded by `"{seed}:{language}:{tier}"` alone, so the order of a
stratum never depends on which other strata were drawn, or when.
"""
from __future__ import annotations

import json
import random
import subprocess
import time

# Our label -> GitHub `language:` qualifier. The label is also the detected-language key
# (measure.EXT_LANG), so a target per language counts the same thing that was drawn.
LANGS = {
    "java": "Java", "python": "Python", "javascript": "JavaScript",
    "typescript": "TypeScript", "go": "Go", "c": "C", "c++": "C++",
    "c#": "C#", "ruby": "Ruby", "php": "PHP", "rust": "Rust",
}
# Popularity tiers, a proxy for the spread from heavily-maintained to rougher code. Disjoint:
# a repository with exactly 1000 stars belongs to one tier only.
TIERS = {"high": "stars:>5000", "mid": "stars:1000..5000", "low": "stars:200..999"}
TIER_BANDS = {
    "high": ["5001..7500", "7501..12000", "12001..20000", "20001..40000", ">40000"],
    "mid": ["1000..1400", "1401..2000", "2001..2800", "2801..3800", "3801..5000"],
    "low": ["200..290", "291..400", "401..560", "561..760", "761..999"],
}

RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 60.0


def query(lang: str, band: str, exclude: set[str]) -> str:
    return (f"language:{LANGS[lang]} stars:{band} archived:false fork:false"
            + "".join(f" -user:{o}" for o in sorted(exclude)))


def gh_search(q: str, per_page: int, page: int) -> dict:
    """One page of GitHub search/repositories, stars descending, via the authenticated `gh`
    CLI (for the higher search rate limit). A rate-limit refusal is waited out and retried,
    so a transient limit is not recorded as an empty band."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        out = subprocess.run(
            ["gh", "api", "-X", "GET", "search/repositories",
             "-f", f"q={q}", "-f", "sort=stars", "-f", "order=desc",
             "-f", f"per_page={per_page}", "-f", f"page={page}"],
            capture_output=True, text=True)
        if out.returncode == 0:
            try:
                return json.loads(out.stdout)
            except ValueError as e:
                return {"total_count": 0, "items": [], "error": str(e)}
        err = out.stderr.strip()
        if "rate limit" in err.lower() and attempt < RATE_LIMIT_RETRIES:
            time.sleep(RATE_LIMIT_WAIT)
            continue
        return {"total_count": 0, "items": [], "error": err[:200]}
    return {"total_count": 0, "items": [], "error": "rate limited"}


def band_candidates(lang: str, band: str, pool: int, exclude: set[str], pause: float,
                    search=gh_search) -> dict:
    """Up to `pool` candidates for one star band, stars descending, across result pages.
    Owners in `exclude` are dropped both by the query qualifier and again after the fetch."""
    q = query(lang, band, exclude)
    items, total, err, page = [], 0, None, 1
    while len(items) < pool and page <= 10:          # GitHub caps search at 1000 results
        res = search(q, min(100, pool - len(items)), page)
        total = res.get("total_count", total)
        if res.get("error"):
            err = res["error"]
            break
        batch = [it for it in res.get("items", [])
                 if it.get("full_name")
                 and it["full_name"].split("/")[0].lower() not in exclude]
        if not batch:
            break
        items.extend(batch)
        page += 1
        time.sleep(pause)
    return {"band": band, "query": q, "total_matched": total, "error": err,
            "candidates": [{"full_name": it["full_name"], "url": it["clone_url"],
                            "github_language": it.get("language"),
                            "stars": it.get("stargazers_count"), "rank": r, "star_band": band}
                           for r, it in enumerate(items, 1)]}


def stratum(lang: str, tier: str, seed: int, pool: int, exclude: set[str], pause: float,
            search=gh_search) -> dict:
    """The frozen frame of one stratum: every band's query, match count and fetched candidates
    (in rank order, as fetched), plus the seeded draw order over all of them."""
    bands = [band_candidates(lang, b, max(1, -(-pool // len(TIER_BANDS[tier]))), exclude,
                             pause, search)
             for b in TIER_BANDS[tier]]
    return {"language": lang, "tier": tier, "bands": bands,
            "order": draw_order(bands, f"{seed}:{lang}:{tier}")}


def draw_order(bands: list[dict], seed_key: str) -> list[dict]:
    rng = random.Random(seed_key)
    lists = {}
    for b in bands:
        cands = list(b["candidates"])
        rng.shuffle(cands)
        lists[b["band"]] = cands
    weights = {b["band"]: max(b["total_matched"] or 0, 1) for b in bands}
    names = [b["band"] for b in bands]
    order = []
    while any(lists.values()):
        live = [n for n in names if lists[n]]
        n = rng.choices(live, weights=[weights[x] for x in live])[0]
        order.append(lists[n].pop(0))
    return order
