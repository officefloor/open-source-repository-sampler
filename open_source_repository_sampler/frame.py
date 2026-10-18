"""The sampling frame: the whole population of each stratum, enumerated, and its seeded draw order.

A stratum is (GitHub language, popularity tier). GitHub search returns at most 1000 results per
query, sorted, so fetching a tier with one query would give only its most-starred top. Instead
each tier is split into bands until every band matches at most 1000 repositories, and every
band is then fetched whole. The frame is therefore the complete population of the stratum as
GitHub search lists it, every candidate has the same chance of being drawn, and a seeded
shuffle of that enumeration is a simple random sample within the stratum.

Bands are split on stars first (halving the star range). A single star value that still
matches more than 1000 repositories is split on creation date. A band that cannot be split
further is fetched to the 1000 cap and marked `truncated`, which the derivation reports.

The shuffle for a stratum is seeded by `"{seed}:{language}:{tier}"` alone, over the candidates
sorted by name, so the order of a stratum never depends on which other strata were drawn, on
when, or on the order GitHub happened to return the pages in.

A search that fails, or that GitHub flags as incomplete, is retried, and if it still fails the
run stops (`FrameError`) rather than freezing a frame with a band missing.
"""
from __future__ import annotations

import datetime
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
# Popularity tiers by stars. Stars measure attention, not quality or maintenance; the tiers
# exist so a sample spans the range of attention rather than only its top. Disjoint: a
# repository with exactly 1000 stars belongs to one tier only. (lo, hi); hi None = unbounded.
TIER_STARS = {"high": (5001, None), "mid": (1000, 5000), "low": (200, 999)}
TIERS = {t: f"stars:{_lo}..{_hi}" if _hi else f"stars:>={_lo}"
         for t, (_lo, _hi) in TIER_STARS.items()}

SEARCH_CAP = 1000            # GitHub returns at most this many results per query
PAGE = 100                   # results per page; always this, so pages never overlap
CREATED_FROM = datetime.date(2007, 10, 1)   # GitHub's first repositories

RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 60.0
INCOMPLETE_RETRIES = 3
INCOMPLETE_WAIT = 5.0


class FrameError(RuntimeError):
    """GitHub search failed for a band, so the frame would be incomplete. The run stops."""


def _stars(band: dict) -> str:
    lo, hi = band["stars"]
    return f"{lo}..{hi}" if hi is not None else f">={lo}"


def label(band: dict) -> str:
    s = _stars(band)
    return s if not band.get("created") else f"{s} created:{band['created'][0]}..{band['created'][1]}"


def query(lang: str, band: dict, exclude: set[str]) -> str:
    q = f"language:{LANGS[lang]} stars:{_stars(band)}"
    if band.get("created"):
        q += f" created:{band['created'][0]}..{band['created'][1]}"
    return q + " archived:false fork:false" + "".join(f" -user:{o}" for o in sorted(exclude))


def split(band: dict) -> tuple[dict, dict] | None:
    """Two disjoint halves of `band`, by stars, or by creation date once the band is a single
    star value. None when it cannot be split further."""
    lo, hi = band["stars"]
    if hi is None:
        mid = max(lo, 2 * lo)                # open-ended: peel off a doubling range
        return ({**band, "stars": (lo, mid)}, {**band, "stars": (mid + 1, None)})
    if lo < hi:
        mid = (lo + hi) // 2
        return ({**band, "stars": (lo, mid)}, {**band, "stars": (mid + 1, hi)})
    c_lo, c_hi = band.get("created") or (CREATED_FROM.isoformat(),
                                         datetime.date.today().isoformat())
    d_lo, d_hi = datetime.date.fromisoformat(c_lo), datetime.date.fromisoformat(c_hi)
    if d_lo >= d_hi:
        return None
    d_mid = d_lo + (d_hi - d_lo) // 2
    return ({**band, "created": (c_lo, d_mid.isoformat())},
            {**band, "created": ((d_mid + datetime.timedelta(days=1)).isoformat(), c_hi)})


def gh_search(q: str, per_page: int, page: int) -> dict:
    """One page of GitHub search/repositories, stars descending, via the authenticated `gh`
    CLI (for the higher search rate limit). A rate-limit refusal is waited out and retried. A
    response GitHub flags `incomplete_results` (its search timed out part way) is retried too,
    so a partial page is never taken for the whole."""
    err = "?"
    rate_tries = incomplete_tries = 0
    while True:
        out = subprocess.run(
            ["gh", "api", "-X", "GET", "search/repositories",
             "-f", f"q={q}", "-f", "sort=stars", "-f", "order=desc",
             "-f", f"per_page={per_page}", "-f", f"page={page}"],
            capture_output=True, text=True)
        if out.returncode == 0:
            try:
                res = json.loads(out.stdout)
            except ValueError as e:
                return {"total_count": 0, "items": [], "error": f"bad JSON: {e}"}
            if res.get("incomplete_results") and incomplete_tries < INCOMPLETE_RETRIES:
                incomplete_tries += 1
                time.sleep(INCOMPLETE_WAIT)
                continue
            if res.get("incomplete_results"):
                res["error"] = "GitHub returned incomplete results (search timed out)"
            return res
        err = out.stderr.strip()
        if "rate limit" in err.lower() and rate_tries < RATE_LIMIT_RETRIES:
            rate_tries += 1
            time.sleep(RATE_LIMIT_WAIT)
            continue
        return {"total_count": 0, "items": [], "error": err[:200] or "gh api failed"}


def _count(lang: str, band: dict, exclude: set[str], search) -> int:
    res = search(query(lang, band, exclude), 1, 1)
    if res.get("error"):
        raise FrameError(f"GitHub search failed for {lang} {label(band)}: {res['error']}")
    return int(res.get("total_count") or 0)


def leaves(lang: str, band: dict, exclude: set[str], pause: float, search=gh_search,
           log=lambda *_: None) -> list[tuple[dict, int]]:
    """`band` split until each piece matches at most SEARCH_CAP repositories: (band, count)."""
    total = _count(lang, band, exclude, search)
    time.sleep(pause)
    if total <= SEARCH_CAP:
        return [(band, total)]
    halves = split(band)
    if halves is None:
        log(f"  WARNING {lang} {label(band)}: {total} repositories match and the band cannot "
            f"be split further; TRUNCATED to the top {SEARCH_CAP} by stars")
        return [(band, total)]
    return (leaves(lang, halves[0], exclude, pause, search, log)
            + leaves(lang, halves[1], exclude, pause, search, log))


def band_candidates(lang: str, band: dict, total: int, exclude: set[str], pause: float,
                    search=gh_search) -> dict:
    """Every candidate of one band (all of its pages, PAGE results each). Owners in `exclude`
    are dropped both by the query qualifier and again after the fetch."""
    q = query(lang, band, exclude)
    items, seen, page = [], set(), 1
    while len(items) < min(total, SEARCH_CAP) and page <= SEARCH_CAP // PAGE:
        res = search(q, PAGE, page)
        if res.get("error"):
            raise FrameError(f"GitHub search failed for {lang} {label(band)} page {page}: "
                             f"{res['error']}")
        batch = res.get("items", [])
        for it in batch:
            name = it.get("full_name")
            if not name or name.split("/")[0].lower() in exclude or name in seen:
                continue
            seen.add(name)
            items.append(it)
        if len(batch) < PAGE:
            break
        page += 1
        time.sleep(pause)
    return {"band": label(band), "query": q, "total_matched": total,
            "truncated": total > SEARCH_CAP, "error": None,
            "candidates": [{"full_name": it["full_name"], "url": it["clone_url"],
                            "github_language": it.get("language"),
                            "stars": it.get("stargazers_count"),
                            "mirror": bool(it.get("mirror_url")),
                            "rank": r, "star_band": label(band)}
                           for r, it in enumerate(items, 1)]}


def stratum(lang: str, tier: str, seed: int, exclude: set[str], pause: float,
            search=gh_search, log=lambda *_: None) -> dict:
    """The frozen frame of one stratum: every band's query, match count and every candidate
    fetched (in rank order), plus the seeded draw order over the whole enumeration."""
    bands = [band_candidates(lang, b, n, exclude, pause, search)
             for b, n in leaves(lang, {"stars": TIER_STARS[tier]}, exclude, pause, search, log)]
    return {"language": lang, "tier": tier, "bands": bands,
            "population": sum(b["total_matched"] for b in bands),
            "order": draw_order(bands, f"{seed}:{lang}:{tier}")}


def draw_order(bands: list[dict], seed_key: str) -> list[dict]:
    """A seeded shuffle of every distinct candidate across `bands`. The shuffle runs over the
    candidates sorted by name, so it depends on the set of candidates and the seed alone: a
    simple random sample of the enumerated population."""
    by_name = {}
    for b in bands:
        for c in b["candidates"]:
            by_name.setdefault(c["full_name"], c)     # a repo whose stars moved between
    order = [by_name[n] for n in sorted(by_name)]     # band fetches appears once
    random.Random(seed_key).shuffle(order)
    return order
