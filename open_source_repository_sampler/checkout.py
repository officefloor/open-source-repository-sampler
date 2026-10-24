"""Use a sample: get each usable repository exactly as it was sampled, and run an experiment on it.

The clone holds nothing after the pinned commit, so an experiment run years later sees exactly
the code, and the history, that was sampled:

  * shallow  only the pinned commit's tree (`git fetch --depth 1 <url> <commit>`);
  * deep     every commit up to and including the pinned commit, and nothing after it
             (`git fetch <url> <commit>`: only that commit and its ancestors are transferred).

Both fetch by commit from the URL directly, with no tags and no remote configured, so no later
commit is in the clone and none can be fetched into it by accident.

`only` restricts the files a clone downloads, for an experiment that reads only some of them
(e.g. `[".java", "pom.xml"]`): an entry starting with `.` is a file extension, matched in any
case; any other is a file name, matched exactly, at any depth. The commit's trees are fetched
without file contents (`--filter=blob:none`), a sparse checkout limits the working tree to the
matching files, and only their contents are then downloaded. Every file it holds is
byte-identical to the same file in a full clone; the others are simply absent. The commit, and
with `deep` its history, are exactly as without `only`, and the clone is left with no remote,
so nothing left out can be fetched into it later.

A commit id is a hash over the commit's content and its parents, so the history up to a pinned
commit can never change: if a project later rewrites its history, the pinned commit is either
still served, with exactly the sampled history, or no longer served, and the clone fails loudly.
A deep clone is also checked against the `n_commits` measured at sampling (when the sample was
drawn with a history criterion), which catches an incomplete clone (a host that served grafted
or partial history).

`for_each` is the loop an experiment runs inside: for each usable repository it clones (shallow
or deep), hands the clone to the experiment, then deletes it, so disk use stays at one
repository at a time. The clone can always be made again from the sample.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import os
import shutil
import subprocess
import tempfile
import time

from . import files
from .measure import network_git


def _git(args: list[str], cwd: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def dir_name(full_name: str) -> str:
    return full_name.replace("/", "__")


def sparse_patterns(only: list[str]) -> list[str]:
    """The sparse-checkout (gitignore-style) patterns for `only`: `.ext` as `*.ext` in any case
    (`.java` -> `*.[jJ][aA][vV][aA]`), a file name as itself. A glob or path is refused, not
    passed through: a pattern that silently matched nothing would hand an experiment an empty
    clone."""
    out = []
    for e in only:
        if (not e or e in (".", "..") or e.startswith(("!", "-", "#"))
                or any(c in e for c in "/*?[]\\")):
            raise ValueError(f"`only` entry {e!r}: give a file extension (.java) or a file "
                             f"name (pom.xml), not a path or glob")
        if e.startswith("."):
            e = "*" + "".join(f"[{c.lower()}{c.upper()}]" if c.lower() != c.upper() else c
                              for c in e)
        out.append(e)
    return out


def _drop_promisor(dest: str) -> None:
    """Remove the remote git records for the URL a partial clone was fetched from, so the clone
    has no remote and nothing left out can be fetched into it later."""
    names = _git(["config", "--name-only", "--get-regexp", r"^remote\..*\.promisor$"],
                 dest).stdout.split("\n")
    for n in filter(None, names):
        _git(["config", "--remove-section", n[:-len(".promisor")]], dest)


def clone_at(url: str, commit: str, dest: str, deep: bool = False,
             expected_commits: int | None = None, timeout: float | None = 3600,
             only: list[str] | None = None) -> str | None:
    """`url` into `dest`, detached at `commit`, holding nothing after it. Shallow: that commit
    only. Deep: all of its history. With `expected_commits` (the sample's `n_commits`), a deep
    clone whose history differs from the one sampled is refused. With `only` (extensions and
    file names), only the matching files are downloaded and checked out. None on success, else
    an error."""
    patterns = sparse_patterns(only) if only else None
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    fetch = ["fetch", "--quiet", "--no-tags", url, commit]
    if not deep:
        fetch[1:1] = ["--depth", "1"]
    if patterns:
        fetch[1:1] = ["--filter=blob:none"]
    co = ["checkout", "--quiet", "--detach", "FETCH_HEAD"]
    steps = [["init", "--quiet"], fetch, co]
    if patterns:
        steps[1:1] = [["sparse-checkout", "set", "--no-cone", *patterns]]

    def unlock():                                       # a checkout killed mid-way leaves this
        try:
            os.unlink(os.path.join(dest, ".git", "index.lock"))
        except OSError:
            pass

    for args in steps:
        if args is fetch:
            err = network_git(fetch, dest, timeout)     # stall-aborted and retried
        elif args is co and patterns:
            # The checkout downloads the matching files' contents: a network step too.
            err = network_git(co, dest, timeout, prepare=unlock)
        else:
            r = _git(args, dest, timeout)
            err = None if r.returncode == 0 else r.stderr.strip()[:160]
        if err:
            return f"git {args[0]} failed: {err}"
    if patterns:
        _drop_promisor(dest)
    head = _git(["rev-parse", "HEAD"], dest).stdout.strip()
    if head != commit:
        return f"landed on {head[:12]}, not {commit[:12]}"
    n_head = int(_git(["rev-list", "--count", "HEAD"], dest).stdout.strip() or 0)
    n_all = int(_git(["rev-list", "--count", "--all"], dest).stdout.strip() or 0)
    if n_all != n_head:
        return f"clone holds {n_all - n_head} commits beyond {commit[:12]}"
    if deep and expected_commits is not None and n_head != expected_commits:
        return (f"history up to {commit[:12]} has {n_head} commits, the sample recorded "
                f"{expected_commits}: the clone is incomplete")
    return None


def _mode(deep: bool, only: list[str] | None) -> str:
    return ("deep" if deep else "shallow") + (f", only {', '.join(only)}" if only else "")


def _only_key(only: list[str] | None) -> list[str]:
    return sparse_patterns(only) if only else []


def _only_of(dest: str) -> list[str]:
    """The sparse patterns a kept clone was made with ([] for a full clone)."""
    if _git(["config", "--get", "core.sparseCheckout"], dest).stdout.strip() != "true":
        return []
    return _git(["sparse-checkout", "list"], dest).stdout.split("\n")[:-1]


def _at(dest: str, commit: str) -> bool:
    if not os.path.isdir(os.path.join(dest, ".git")):
        return False
    return _git(["rev-parse", "HEAD"], dest).stdout.strip() == commit


def _rows(sample: str, langs: list[str] | None) -> list[dict]:
    return [r for r in files.read_sample(sample) if not langs or r["language"] in langs]


def checkout(sample: str, dest: str, langs: list[str] | None = None, deep: bool = False,
             log=print, only: list[str] | None = None) -> list[dict]:
    """Clone the usable repositories of `sample` (optionally only `langs`) into `dest`, and
    keep them; with `only`, only the matching files (see `clone_at`). Returns one
    {full_name, path, commit, error} per repository."""
    _only_key(only)                                     # a bad entry fails before any clone
    os.makedirs(dest, exist_ok=True)
    out = []
    rows = _rows(sample, langs)
    for i, r in enumerate(rows, 1):
        path = os.path.join(dest, dir_name(r["full_name"]))
        if _at(path, r["commit"]) and _only_of(path) == _only_key(only):
            err = None
            log(f"[{i}/{len(rows)}] {r['full_name']}: already at {r['commit'][:12]}")
        else:
            log(f"[{i}/{len(rows)}] {r['full_name']} @ {r['commit'][:12]} "
                f"({_mode(deep, only)}) ...")
            err = clone_at(r["url"], r["commit"], path, deep, r.get("n_commits") or None,
                           only=only)
            if err:
                log(f"        ERROR {err}")
        out.append({"full_name": r["full_name"], "path": path, "commit": r["commit"],
                    "error": err})
    bad = [o for o in out if o["error"]]
    log(f"{len(out) - len(bad)}/{len(out)} repositories at their pinned commit in {dest}")
    return out


@dataclasses.dataclass
class Repository:
    """One usable repository of a sample, cloned at its pinned commit, as an experiment gets it."""
    full_name: str
    url: str
    commit: str
    language: str
    path: str            # the clone; deleted after the experiment returns (unless kept)
    deep: bool           # whether `path` holds the history up to `commit`
    row: dict            # the whole CSV row: stars, tier, criteria measures, ...
    only: list[str] | None = None   # the extensions and file names the clone holds (None: all)


def _done(log_path: str | None) -> set:
    """(full_name, commit) pairs the run log records as done."""
    done = set()
    if log_path and os.path.exists(log_path):
        with open(log_path) as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if rec.get("status") == "done":
                        done.add((rec["full_name"], rec["commit"]))
    return done


def for_each(sample: str, experiment, deep: bool = False, langs: list[str] | None = None,
             work_dir: str | None = None, keep: bool = False, run_log: str | None = None,
             fail_fast: bool = False, log=print, only: list[str] | None = None) -> list[dict]:
    """Run `experiment(repository)` on every usable repository of `sample`, one at a time: clone
    it at its pinned commit (deep or shallow; with `only`, only the matching files, see
    `clone_at`), call the experiment, delete the clone.

    A failure (clone, or the experiment raising) is recorded and the loop moves on, unless
    `fail_fast`. With `run_log`, each outcome is appended to that JSON Lines file and a re-run
    skips the repositories it records as done, so an interrupted run resumes.
    Returns one {full_name, commit, status, error, result, seconds} per repository."""
    _only_key(only)                                     # a bad entry fails before any clone
    rows = _rows(sample, langs)
    done = _done(run_log)
    own_work = work_dir is None
    work = tempfile.mkdtemp(prefix="osrs-run-") if own_work else work_dir
    os.makedirs(work, exist_ok=True)
    out = []
    try:
        for i, r in enumerate(rows, 1):
            tag = f"[{i}/{len(rows)}] {r['full_name']} @ {r['commit'][:12]}"
            if (r["full_name"], r["commit"]) in done:
                log(f"{tag}: done in an earlier run, skipped")
                out.append({"full_name": r["full_name"], "commit": r["commit"],
                            "status": "skipped", "error": None, "result": None, "seconds": 0})
                continue
            path = os.path.join(work, dir_name(r["full_name"]))
            t0 = time.time()
            log(f"{tag}: cloning ({_mode(deep, only)}) ...")
            err = clone_at(r["url"], r["commit"], path, deep, r.get("n_commits") or None,
                           only=only)
            result = None
            if not err:
                log(f"{tag}: running experiment ...")
                try:
                    result = experiment(Repository(r["full_name"], r["url"], r["commit"],
                                                   r["language"], path, deep, r,
                                                   list(only) if only else None))
                except Exception as e:
                    err = f"experiment raised {type(e).__name__}: {e}"
            if not keep:
                shutil.rmtree(path, ignore_errors=True)
            rec = {"full_name": r["full_name"], "commit": r["commit"],
                   "status": "failed" if err else "done", "error": err,
                   "seconds": round(time.time() - t0, 1)}
            log(f"{tag}: {rec['status']}" + (f" ({err})" if err else "")
                + f" in {rec['seconds']}s")
            if run_log:
                with open(run_log, "a") as fh:
                    fh.write(json.dumps({**rec, "finished_utc": datetime.datetime.now(
                        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
            out.append({**rec, "result": result})
            if err and fail_fast:
                break
    finally:
        if own_work and not keep:
            shutil.rmtree(work, ignore_errors=True)
    n = {s: sum(1 for o in out if o["status"] == s) for s in ("done", "skipped", "failed")}
    log(f"{n['done']} done, {n['skipped']} skipped (already done), {n['failed']} failed "
        f"of {len(rows)} repositories")
    return out


# Placeholders a `run` command may use in its arguments, and the environment variables every
# run command gets, for experiments not written in Python.
def _fields(repo: Repository) -> dict:
    return {"path": repo.path, "url": repo.url, "commit": repo.commit,
            "full_name": repo.full_name, "language": repo.language,
            "deep": "true" if repo.deep else "false"}


def command_experiment(command: list[str]):
    """An experiment that runs `command` once per repository: `{path}`, `{url}`, `{commit}`,
    `{full_name}`, `{language}` and `{deep}` in its arguments are replaced by the repository's
    values, which are also in the environment as SAMPLED_REPOSITORY_PATH, ..._URL, ..._COMMIT,
    ..._FULL_NAME, ..._LANGUAGE and ..._DEEP. A non-zero exit is a failure."""
    def run(repo: Repository) -> int:
        fields = _fields(repo)
        args = []
        for a in command:
            for k, v in fields.items():
                a = a.replace("{" + k + "}", v)
            args.append(a)
        env = {**os.environ, **{f"SAMPLED_REPOSITORY_{k.upper()}": v for k, v in fields.items()}}
        code = subprocess.run(args, env=env).returncode
        if code != 0:
            raise RuntimeError(f"command exited {code}")
        return code
    return run


def verify(sample: str, fetch: bool = False, log=print) -> list[str]:
    """Problems with a sample; empty when it is intact. Checks the CSV against the hash in its
    derivation, and with `fetch`, that every pinned commit can still be fetched."""
    if os.path.isdir(sample):
        sample = os.path.join(sample, files.names()[0])
    deriv = sample[:-len(".csv")] + ".derivation.json"
    problems = []
    if not sample.endswith(".csv") or not os.path.exists(deriv):
        return [f"no derivation file beside {sample} (expected {deriv})"]
    with open(deriv) as fh:
        d = json.load(fh)
    got = files.sha256(sample)
    if got != d.get("csv_sha256"):
        problems.append(f"{os.path.basename(sample)} sha256 {got} does not match the "
                        f"derivation's {d.get('csv_sha256')}: the CSV was changed after sampling")
    else:
        log(f"OK  {os.path.basename(sample)} matches its derivation (sha256 {got[:16]}...)")
    s = d.get("sampler", {})
    log(f"    drawn by {s.get('name')} {s.get('version')} @ {s.get('commit')}"
        + ("  (DIRTY)" if s.get("dirty") else ""))
    rows = files.read_sample(sample)
    commits = {r["sampler_commit"] for r in rows}
    if commits - {s.get("commit") or ""}:
        problems.append(f"rows name sampler commits {sorted(commits)}, the derivation "
                        f"{s.get('commit')}")
    if fetch:
        with tempfile.TemporaryDirectory(prefix="osrs-verify-") as tmp:
            _git(["init", "--quiet", "--bare"], tmp)
            for r in rows:
                # Stall-aborted and retried, so a transient outage is not reported as a lost
                # commit. The commit object alone (`tree:0`) shows it is still served; a host
                # without filter support sends the whole tree instead, which shows the same.
                err = network_git(["fetch", "--depth", "1", "--filter=tree:0", "--quiet",
                                   r["url"], r["commit"]], tmp, 600)
                if err:
                    problems.append(f"{r['full_name']}: commit {r['commit'][:12]} can no "
                                    f"longer be fetched ({err})")
                else:
                    log(f"OK  {r['full_name']} @ {r['commit'][:12]}")
    return problems
