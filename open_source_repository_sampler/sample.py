"""Draw a sample: seeded, stratified, grown until each language has its target of usable
repositories, every repository pinned to the commit that met the criteria.

The loop, in seeded rounds: every language still short of its target draws the next candidate
from each of its tiers (in that stratum's frozen draw order), clones and measures it, and
decides it usable or excluded by the criteria alone. A language stops the moment it has
`target` usable repositories, or when all of its strata are exhausted (reported, not hidden).
Usable repositories count toward the language DETECTED in their source, not the stratum they
were drawn from, so a C++ project labelled C on GitHub counts as C++.

Every candidate drawn is written to the CSV, usable or not, so the sample shows that nothing
was hand-picked. A candidate that could not be inspected (its clone failed) is `error`, not
`excluded`: loss to infrastructure is kept apart from exclusion by the criteria, because it
can fall unevenly (a clone timeout falls hardest on the largest projects). Progress is
checkpointed after every candidate, so an interrupted run resumes where it stopped, from the
same frozen frame, by re-running the same command.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile

from . import files, frame, measure, provenance

BATCH_PER_TIER = 1           # candidates drawn per tier, per short language, per round

# The criteria: (parameter, measured column, comparison). A repository is usable when it was
# measured, its detected language is one of the sampled languages, and it meets every bound.
CRITERIA = [
    ("min_functions", "n_functions", "functions parsed by lizard (tests excluded)"),
    ("min_files_with_functions", "n_files_with_functions", "source files holding a function"),
    ("min_source_files", "n_source_files", "source files of the project's own code"),
    ("min_nloc", "nloc", "non-comment lines of code"),
    ("min_commits", "n_commits", "commits reachable from the pinned commit"),
    ("min_contributors", "n_contributors", "distinct author emails in that history"),
    ("min_language_share", "language_share", "detected language's share of source files"),
]

# Criteria on the history. Only when one of these is set does a candidate's clone fetch the
# full history; otherwise a shallow clone is all the criteria need, and the history columns are
# left empty rather than guessed.
HISTORY_CRITERIA = ("min_commits", "min_contributors")

DEFAULTS = {
    "langs": list(frame.LANGS),
    "tiers": list(frame.TIERS),
    "target": 30,
    "seed": 1,
    "exclude_owners": [],
    "min_functions": 50,
    "min_files_with_functions": 15,
    "min_source_files": 0,
    "min_nloc": 0,
    "min_commits": 0,
    "min_contributors": 0,
    "min_language_share": 0.0,
    "max_clones": 2000,
    "pause": 2.0,
}
# Parameters that shape WHICH repositories are sampled. A resumed run must match these
# exactly; `pause` and `max_clones` only pace or bound the run.
_SHAPING = [k for k in DEFAULTS if k not in ("pause", "max_clones")]

STATUSES = ("usable", "excluded", "error")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def needs_history(params: dict) -> bool:
    return any(params[p] > 0 for p in HISTORY_CRITERIA)


def clone_notice(params: dict) -> str:
    """Said before any download, so the cost of the run is known up front."""
    set_ = [f"--{p.replace('_', '-')} {params[p]}" for p in HISTORY_CRITERIA if params[p] > 0]
    if set_:
        return ("NOTE: FULL-HISTORY clones (every commit, blobless) because of "
                + ", ".join(set_) + ". Downloads are larger and slower than shallow clones, "
                "most of all for long-lived projects. Drop the history criteria for shallow "
                "clones.")
    return ("NOTE: SHALLOW clones (pinned commit only), as no history criterion "
            "(--min-commits, --min-contributors) is set: the lightest download that measures "
            "these criteria exactly. History columns will be empty.")


def frame_notice(params: dict) -> str:
    return (f"NOTE: the whole population of each stratum ({len(params['langs'])} languages x "
            f"{len(params['tiers'])} tiers) is enumerated from GitHub search first, about one "
            f"request per {frame.PAGE} repositories, so every repository has the same chance "
            f"of being drawn. Large strata (a popular language's low tier) take minutes.")


def judge(row: dict, params: dict) -> tuple[str, str]:
    """(status, reason) for a measured row, by the criteria alone."""
    if row.get("error"):
        return "error", row["error"]
    if row.get("language") not in params["langs"]:
        return "excluded", f"language {row.get('language') or 'none'} not sampled"
    failed = [f"{col} {row.get(col)} < {params[p]}"
              for p, col, _ in CRITERIA if (row.get(col) or 0) < params[p]]
    return ("excluded", "; ".join(failed)) if failed else ("usable", "")


class Progress:
    """Append-only checkpoint: the parameters, each stratum's frozen frame, each row."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> tuple[dict | None, dict, list[dict], str | None, dict | None]:
        params, strata, rows, started, sampler = None, {}, [], None, None
        if not os.path.exists(self.path):
            return params, strata, rows, started, sampler
        with open(self.path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                kind = rec.pop("type")
                if kind == "parameters":
                    params, started = rec["parameters"], rec["started_utc"]
                    sampler = rec["sampler"]
                elif kind == "stratum":
                    strata[(rec["language"], rec["tier"])] = rec
                elif kind == "row":
                    rows.append(rec)
        return params, strata, rows, started, sampler

    def add(self, kind: str, rec: dict) -> None:
        with open(self.path, "a") as fh:
            fh.write(json.dumps({"type": kind, **rec}) + "\n")


def run(params: dict, out_dir: str, label: str | None, argv: list[str],
        allow_dirty: bool = False, overwrite: bool = False, search=frame.gh_search,
        measure_fn=measure.measure, log=print) -> dict:
    """Draw the sample described by `params` into `out_dir`. Returns the derivation."""
    unknown = [lang for lang in params["langs"] if lang not in frame.LANGS]
    if unknown:
        raise ValueError(f"unknown languages {unknown}; choose from {list(frame.LANGS)}")
    bad_tiers = [t for t in params["tiers"] if t not in frame.TIERS]
    if bad_tiers:
        raise ValueError(f"unknown tiers {bad_tiers}; choose from {list(frame.TIERS)}")
    sampler = provenance.sampler()
    if sampler["dirty"] and not allow_dirty:
        raise RuntimeError(
            "the sampler has uncommitted changes (or its commit is unknown), so its commit "
            "would not describe the code that drew this sample. Commit them, or pass "
            "--allow-dirty to record the sample as drawn by a dirty sampler.")
    measure.check_parser()

    os.makedirs(out_dir, exist_ok=True)
    csv_name, deriv_name, prog_name = files.names(label)
    csv_path, deriv_path = os.path.join(out_dir, csv_name), os.path.join(out_dir, deriv_name)
    prog = Progress(os.path.join(out_dir, prog_name))
    exclude = {o.lower() for o in params["exclude_owners"]}
    history = needs_history(params)
    log(clone_notice(params))
    log(frame_notice(params))
    shaping = {k: params[k] for k in _SHAPING}

    old, strata, rows, started, old_sampler = prog.load()
    if old is None:
        for p in (csv_path, deriv_path):
            if os.path.exists(p) and not overwrite:
                raise FileExistsError(f"{p} exists; a sample is never silently replaced. "
                                      f"Use another --out-dir or --label, or pass --overwrite.")
        started = _now()
        prog.add("parameters", {"parameters": shaping, "started_utc": started,
                                "sampler": sampler})
    elif old != shaping:
        raise RuntimeError(f"{prog.path} holds a run with different parameters; finish it with "
                           f"its original command, or delete that file to start afresh.")
    elif (old_sampler or {}).get("commit") != sampler["commit"]:
        raise RuntimeError(f"{prog.path} was started by sampler commit "
                           f"{(old_sampler or {}).get('commit')}, not {sampler['commit']}; one "
                           f"sample is drawn by one sampler. Resume with that commit, or delete "
                           f"the file to start afresh.")
    else:
        log(f"resuming: {len(rows)} candidates already drawn")

    def stratum(lang: str, tier: str) -> dict:
        key = (lang, tier)
        if key not in strata:
            log(f"  enumerating GitHub: {lang}/{tier} ...")
            # A search failure raises FrameError here, before anything is checkpointed for
            # this stratum, so a re-run enumerates it afresh instead of freezing a gap.
            s = frame.stratum(lang, tier, params["seed"], exclude, params["pause"], search, log)
            prog.add("stratum", s)
            strata[key] = s
            trunc = [b["band"] for b in s["bands"] if b["truncated"]]
            log(f"  {lang}/{tier}: {s['population']} in the population, {len(s['order'])} "
                f"enumerated in {len(s['bands'])} band(s)"
                + (f"  TRUNCATED bands: {trunc}" if trunc else ""))
        return strata[key]

    drawn = {r["url"] for r in rows}
    cursor = {}
    for r in rows:                       # a resumed run picks up each stratum where it stopped
        key = (r["stratum_language"], r["tier"])
        cursor[key] = max(cursor.get(key, 0), r["cursor"])
    work = tempfile.mkdtemp(prefix="osrs-")
    exhausted: set = set()

    def usable_by_lang() -> dict:
        by = {lang: 0 for lang in params["langs"]}
        for r in rows:
            if r["status"] == "usable":
                by[r["language"]] += 1
        return by

    try:
        rnd = 0
        while len(rows) < params["max_clones"]:
            rnd += 1
            by = usable_by_lang()
            short = [lang for lang in params["langs"]
                     if lang not in exhausted and by[lang] < params["target"]]
            log(f"round {rnd}: drawn {len(rows)}  "
                + "  ".join(f"{lang}:{by[lang]}/{params['target']}" for lang in params["langs"]))
            if not short:
                break
            drew_any = False
            for lang in short:
                took = 0
                for tier in params["tiers"]:
                    if usable_by_lang()[lang] >= params["target"]:
                        break            # stop at the target exactly, not at the round's end
                    order = stratum(lang, tier)["order"]
                    key = (lang, tier)
                    took_tier = 0
                    while cursor.get(key, 0) < len(order) and took_tier < BATCH_PER_TIER:
                        cand = order[cursor.get(key, 0)]
                        cursor[key] = cursor.get(key, 0) + 1
                        if cand["url"] in drawn:
                            continue
                        dest = os.path.join(work, "repo")
                        log(f"  [{len(rows) + 1}] {lang}/{tier} {cand['full_name']} "
                            f"({cand['stars']} stars) ...")
                        m = measure_fn(cand["url"], dest, history=history,
                                       hint=cand.get("github_language"))
                        shutil.rmtree(dest, ignore_errors=True)
                        row = {"draw": len(rows) + 1, **cand, "stratum_language": lang,
                               "tier": tier, "cursor": cursor[key], **m,
                               "sampler_version": sampler["version"],
                               "sampler_commit": sampler["commit"]}
                        row["status"], row["reason"] = judge(row, params)
                        log(f"        -> {row['status']}"
                            + (f" ({row['reason']})" if row["reason"] else
                               f" {row['language']} @ {row['commit'][:12]}"))
                        rows.append(row)
                        drawn.add(cand["url"])
                        prog.add("row", row)
                        took_tier += 1
                        drew_any = True
                        if len(rows) >= params["max_clones"]:
                            break
                    took += took_tier
                    if len(rows) >= params["max_clones"]:
                        break
                if took == 0 and all(cursor.get((lang, t), 0) >= len(stratum(lang, t)["order"])
                                     for t in params["tiers"]):
                    exhausted.add(lang)
                    log(f"  {lang}: strata exhausted at {usable_by_lang()[lang]} usable")
                if len(rows) >= params["max_clones"]:
                    break
            if not drew_any:
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)

    files.write_csv(csv_path, rows)
    derivation = _derivation(params, rows, strata, exhausted, sampler, started, argv,
                             label, csv_name, files.sha256(csv_path), out_dir)
    with open(deriv_path, "w") as fh:
        json.dump(derivation, fh, indent=2)
        fh.write("\n")
    os.remove(prog.path)
    by = usable_by_lang()
    n_err = sum(1 for r in rows if r["status"] == "error")
    log(f"\nwrote {csv_path} ({len(rows)} drawn, {sum(by.values())} usable, {n_err} errors)")
    log(f"wrote {deriv_path}")
    for lang in params["langs"]:
        state = "exhausted" if lang in exhausted else (
            "ok" if by[lang] >= params["target"] else "short (max clones reached)")
        log(f"  {lang:11} usable {by[lang]:>3}/{params['target']}  [{state}]")
    return derivation


def _flags(params: dict) -> list[str]:
    """The fully resolved command-line flags for `params` (defaults written out)."""
    out = []
    for k, v in params.items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, list):
            out += [flag, ",".join(v)] if v else []
        else:
            out += [flag, str(v)]
    return out


def _derivation(params, rows, strata, exhausted, sampler, started, argv, label, csv_name,
                csv_sha, out_dir) -> dict:
    import shlex
    by = {lang: 0 for lang in params["langs"]}
    per_stratum = {}
    for s in sorted(strata):
        key = f"{s[0]}/{s[1]}"
        per_stratum[key] = {"population": strata[s]["population"],
                            "enumerated": len(strata[s]["order"]), "drawn": 0,
                            "usable": 0, "excluded": 0, "error": 0}
    for r in rows:
        if r["status"] == "usable":
            by[r["language"]] += 1
        key = f"{r['stratum_language']}/{r['tier']}"
        per_stratum[key]["drawn"] += 1
        per_stratum[key][r["status"]] += 1
    truncated = [f"{s[0]}/{s[1]} {b['band']} ({b['total_matched']} matched)"
                 for s in sorted(strata) for b in strata[s]["bands"] if b["truncated"]]
    resolved = [provenance.NAME, "sample", *_flags(params), "--out-dir", out_dir]
    if label:
        resolved += ["--label", label]
    commit = sampler["commit"] or "<unknown commit>"
    about = provenance.about(sampler)
    about["what"] = about["what"].replace("{csv}", csv_name)
    return {
        "about": about,
        "derivation_of": csv_name,
        "csv_sha256": csv_sha,
        "sampler": {**sampler, "tools": provenance.tools()},
        "command": shlex.join([provenance.NAME, *argv]),
        "resolved_command": shlex.join(resolved),
        "parameters": params,
        # How every candidate was cloned for inspection, stated outright rather than left to
        # be inferred from the parameters and the sampler's version.
        "sampling_clone": {
            "mode": "full-history" if needs_history(params) else "shallow",
            "git_clone": " ".join(["git", "clone", *measure.clone_flags(needs_history(params)),
                                     "<url>", "<dir>"]),
            "because": ([f"{p} = {params[p]}" for p in HISTORY_CRITERIA if params[p] > 0]
                        or ["no history criterion is set (" + ", ".join(HISTORY_CRITERIA)
                            + " are 0)"]),
            "history_columns_measured": needs_history(params),
            "history_columns": ["n_commits", "n_contributors", "first_commit_date"],
        },
        "reproduce": {
            "the_same_code": (f"{provenance.NAME} checkout {csv_name} --dest repos  "
                              f"# every usable repository at its pinned commit"),
            "the_method": [
                f"git clone {sampler['repository']}.git",
                f"cd {provenance.NAME}",
                f"git checkout {commit}",
                "python3 -m venv .venv && .venv/bin/pip install .",
                ".venv/bin/" + shlex.join(resolved),
            ],
            "note": ("Re-running the method draws afresh from GitHub as it is then: stars change "
                     "and repositories come and go, so it reproduces HOW the sample was drawn, "
                     "not necessarily the identical list. The CSV, with every repository pinned "
                     "to a commit, is the record of WHAT was drawn."),
        },
        "started_utc": started,
        "finished_utc": _now(),
        "method": {
            "frame": ("GitHub search/repositories per stratum (language x popularity tier), "
                      "non-archived non-fork repositories, the excluded owners removed. Each "
                      "tier is split into star bands (then creation-date bands) until every "
                      f"band matches at most {frame.SEARCH_CAP} repositories (GitHub's cap per "
                      "query), and every band is fetched whole, so the frame is the complete "
                      "population of the stratum as GitHub search lists it. A band that could "
                      "not be split below the cap is marked truncated; see summary.truncated_"
                      "bands. A failed search stops the run rather than leaving a gap."),
            "draw_order": ("per stratum, the enumerated candidates sorted by name and shuffled "
                           "by Python's random.Random seeded with '<seed>:<language>:<tier>': "
                           "a simple random sample within the stratum, independent of every "
                           "other stratum."),
            "growth": (f"in rounds, each language short of its target draws the next "
                       f"{BATCH_PER_TIER} candidate of each of its tiers in turn (tiers in the "
                       f"order given); a language stops the moment it has `target` usable "
                       f"repositories, or when its strata are exhausted."),
            "allocation": ("disproportionate: tiers are drawn equally regardless of population, "
                           "so the pooled sample over-represents high-star repositories, and "
                           "the usable split across tiers follows each tier's exclusion rate. "
                           "For estimates about the population, weight each stratum by "
                           "population / usable from summary.per_stratum. Comparisons between "
                           "tiers need no weights."),
            "cross_stratum": ("a usable repository counts toward its detected language, not "
                              "the GitHub label it was drawn under, so a repository mislabelled "
                              "on GitHub enters with its label's stratum's sampling fraction; "
                              "the CSV records both (language, stratum_language)."),
            "pin": ("each candidate is cloned at its default branch as it is when drawn; that "
                    "commit is recorded and is what every measure and criterion describes. The "
                    "clone is deleted once its row is written."),
            "clone": ("full history (blobless): a history criterion is set, so n_commits, "
                      "n_contributors and first_commit_date are measured" if needs_history(params)
                      else "shallow (depth 1): no history criterion is set, so the code at the "
                           "pinned commit is all the criteria need; the history columns are "
                           "left empty"),
            "language_detection": ("dominant language of the project's own source files (tests, "
                                   "vendored and generated code excluded; build-output "
                                   "directories excluded at the repository root only) by file "
                                   "count. `.h` headers are measured but do not vote (they are "
                                   "C or C++ by the other files; an all-header tree takes "
                                   "GitHub's label when it is C or C++, else C). A usable "
                                   "repository counts toward its detected language, not its "
                                   "stratum."),
            "status": ("usable: measured and met every criterion; excluded: measured and did "
                       "not (reason says which); error: could not be inspected (clone failed), "
                       "kept apart because such loss can fall unevenly, e.g. on the largest "
                       "projects when the full history is cloned."),
            "usable_rule": ("measured; detected language in `langs`; and "
                            + "; ".join(f"{col} >= {p} ({desc})" for p, col, desc in CRITERIA)
                            + ". Decided on these generic measures only, before any study "
                              "looks at the repository. Function counts come from lizard and "
                              "are not comparable across languages (it counts different "
                              "constructs per language), so a fixed bound is a different "
                              "threshold per language."),
        },
        "tier_qualifiers": {t: frame.TIERS[t] for t in params["tiers"]},
        "summary": {
            "drawn": len(rows),
            "usable": sum(by.values()),
            "excluded": sum(1 for r in rows if r["status"] == "excluded"),
            "error": sum(1 for r in rows if r["status"] == "error"),
            "usable_per_language": by,
            "per_stratum": per_stratum,
            "truncated_bands": truncated,
            "exhausted_languages": sorted(exhausted),
            "short_languages": sorted(lang for lang in params["langs"]
                                      if by[lang] < params["target"] and lang not in exhausted),
        },
        # The frozen frame: every query, its match count and every candidate fetched, in rank
        # order, so the seeded draw order can be re-derived and audited offline.
        "frame": [{"language": s["language"], "tier": s["tier"],
                   "population": s["population"],
                   "bands": [{"band": b["band"], "query": b["query"],
                              "total_matched": b["total_matched"], "truncated": b["truncated"],
                              "candidates": [[c["full_name"], c["stars"]]
                                             for c in b["candidates"]]}
                             for b in s["bands"]]}
                  for _, s in sorted(strata.items())],
    }
