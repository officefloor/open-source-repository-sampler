"""The two sample files, their fixed names, and reading a sample back.

The names are fixed so a sample can be dropped into a paper's ancillary data unchanged and be
recognised on sight:

  open-source-repository-sample.csv              one row per candidate drawn
  open-source-repository-sample.derivation.json  how the sample was derived, and how to redo it

An optional label is inserted before the suffix (`open-source-repository-sample.java-v2.csv`)
so several samples can sit side by side without breaking the convention.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re

STEM = "open-source-repository-sample"

# Column order of the CSV. Every row of every sample has all of these.
COLUMNS = [
    "draw",                  # 1-based order in which the candidate was drawn
    "status",                # usable | excluded
    "reason",                # why excluded (empty when usable)
    "url",                   # clone URL
    "full_name",             # owner/name
    "commit",                # the pinned commit: the exact code checked against the criteria
    "commit_date",           # committer date of that commit (ISO 8601)
    "language",              # language detected from the project's own source
    "language_share",        # that language's fraction of the source files
    "github_language",       # GitHub's label at sampling time
    "stratum_language",      # the stratum the candidate was drawn from
    "tier",
    "star_band",
    "stars",                 # at sampling time
    "rank",                  # rank by stars within its star band's search results
    "n_source_files",
    "n_functions",
    "n_files_with_functions",
    "nloc",
    # The history columns are measured only when a history criterion (--min-commits,
    # --min-contributors) is set, which is when the full history is cloned; else empty.
    "n_commits",             # reachable from the pinned commit
    "n_contributors",        # distinct author emails reachable from the pinned commit
    "first_commit_date",
    "sampler_version",
    "sampler_commit",        # the commit of the sampler that produced this row
]
INT_COLUMNS = {"draw", "stars", "rank", "n_source_files", "n_functions",
               "n_files_with_functions", "nloc", "n_commits", "n_contributors"}
FLOAT_COLUMNS = {"language_share"}

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def check_label(label: str | None) -> None:
    if label is not None and (not _LABEL.match(label) or label.endswith(".derivation")):
        raise ValueError(f"label {label!r}: use letters, digits, '.', '_' or '-' only")


def names(label: str | None = None) -> tuple[str, str, str]:
    """(csv, derivation, progress) file names for a sample."""
    check_label(label)
    stem = STEM if not label else f"{STEM}.{label}"
    return f"{stem}.csv", f"{stem}.derivation.json", f".{stem}.progress.jsonl"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in COLUMNS})


def read_sample(path: str, usable_only: bool = True) -> list[dict]:
    """The rows of a sample CSV (only the usable ones by default), numbers typed. `path` is the
    CSV itself or the directory holding an unlabelled sample."""
    if os.path.isdir(path):
        path = os.path.join(path, names()[0])
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        if usable_only and r.get("status") != "usable":
            continue
        for c in INT_COLUMNS:
            if r.get(c) not in (None, ""):
                r[c] = int(r[c])
        for c in FLOAT_COLUMNS:
            if r.get(c) not in (None, ""):
                r[c] = float(r[c])
        out.append(r)
    return out
