"""Clone a candidate, pin its commit, detect its language and measure the criteria values.

Every measure here is generic: it describes the repository's size and history, never an
outcome any study tests. That keeps the sampler independent of the studies that use it: a
repository cannot be selected or dropped on a result.

  * language        the dominant language of the project's own source (tests, vendored,
                    generated and build trees excluded), by file count aggregated into
                    languages, with ambiguous `.h` headers resolved per repository;
  * language_share  that language's fraction of those source files;
  * n_source_files, n_functions (lizard), n_files_with_functions, nloc  of that source;
  * n_commits, n_contributors (distinct author emails), first_commit_date  of the history
    reachable from the pinned commit -- measured only when a criterion needs them.

The clone is as light as the criteria allow, without costing accuracy. By default it is
shallow (`--depth 1`): the pinned commit's code is all that the code measures need. Only when a
criterion is on the history is the full history fetched (blobless, `--filter=blob:none`: every
commit, but file contents only for the pinned commit), so history counts are exact. Either way
the clone is deleted once its row is written; an experiment fetches the code again, exactly,
from the pinned commit.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections import Counter

CLONE_TIMEOUT = 600          # seconds; one clone cannot stall the whole run past this

# A dropped connection can leave git waiting indefinitely. A transfer slower than
# STALL_BYTES_PER_SEC for STALL_SECONDS is aborted, and a failed network operation is retried,
# so a transient outage neither hangs an unattended run nor (when sampling) excludes a
# repository that is in fact fine. A failure that is clearly permanent is not retried.
STALL_BYTES_PER_SEC = 1000
STALL_SECONDS = 60
NETWORK_ATTEMPTS = 3
NETWORK_RETRY_WAIT = 30.0    # seconds between attempts
_PERMANENT = ("not found", "could not read username", "authentication failed",
              "access denied", "does not appear to be a git repository")


def network_git(args: list[str], cwd: str | None = None, timeout: float | None = None,
                prepare=None) -> str | None:
    """Run a git command that talks to a remote, aborting a stalled transfer and retrying a
    transient failure. `prepare` runs before each attempt (e.g. to clear a partial clone).
    None on success, else the last error."""
    stall = ["-c", f"http.lowSpeedLimit={STALL_BYTES_PER_SEC}",
             "-c", f"http.lowSpeedTime={STALL_SECONDS}"]
    err = "?"
    for attempt in range(1, NETWORK_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(NETWORK_RETRY_WAIT)
        if prepare:
            prepare()
        try:
            r = subprocess.run(["git", *stall, *args], cwd=cwd, capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            err = f"timeout ({timeout:g}s)"
            continue
        if r.returncode == 0:
            return None
        err = r.stderr.strip()[:160] or "?"
        if any(p in err.lower() for p in _PERMANENT):
            return err
    return f"{err} (after {NETWORK_ATTEMPTS} attempts)"

# Extension -> language. `.h` is deliberately absent: it is ambiguous between C and C++ and
# is resolved per repository in `language` (C++ when the tree holds any unambiguous C++
# file, as GitHub's Linguist does). Values are the frame.LANGS keys.
EXT_LANG = {".java": "java", ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript",
            ".go": "go", ".c": "c", ".cc": "c++", ".cpp": "c++",
            ".cxx": "c++", ".c++": "c++", ".hpp": "c++", ".hh": "c++", ".hxx": "c++",
            ".cs": "c#", ".rb": "ruby", ".php": "php", ".rs": "rust", ".kt": "kotlin",
            ".swift": "swift", ".scala": "scala"}
_CXX_EXTS = (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx")
SOURCE_EXTS = set(EXT_LANG) | {".h"}

_TEST_DIR = re.compile(r"(?:^|/)(?:test|tests|testing|spec|specs|__tests__|testdata|javatests"
                       r"|[^/]+\.(?:tests?|unittests|integrationtests|specs))(?:/|$)", re.I)
# Test files that live BESIDE the code they test, by each language's naming convention.
_TEST_FILE = re.compile(
    r"(?:_test\.go"
    r"|[.-](?:test|spec)\.(?:js|jsx|ts|tsx)"
    r"|(?:^|/)(?:test_[^/]*|[^/]*_test|conftest|tests)\.py"
    r"|(?:^|/)(?:test_[^/]*|[^/]*_(?:test|spec))\.rb"
    r"|_(?:unit)?test\.(?:c|cc|cpp|cxx)"
    r"|(?:^|/)tests\.rs)$", re.I)
# Directories that are not the project's own code: dependencies, build output, vendored or
# generated trees.
_SKIP_DIRS = {".git", "node_modules", "build", "target", "dist", ".venv", "venv",
              "vendor", "vendored", "third_party", "thirdparty", "third-party",
              "generated", "externals", "external", "deps", ".gradle", "out"}
# A source file larger than this is a generated/minified/vendored blob, not hand-maintained.
MAX_FILE_BYTES = 1_000_000

LIZARD_VERSION = "1.23.0"
_PARSER_FIXTURE_PATH = "src/main/java/fixture/Fixture.java"
_PARSER_FIXTURE = """package fixture;

import jakarta.persistence.*;

@Entity
@Table(name = "owners")
public class Fixture {

    @Column(name = "email")
    private String email;

    public String getEmail() {
        return this.email;
    }

    @PrePersist
    void normalise() {
        if (this.email != null) {
            this.email = this.email.toLowerCase();
        }
    }
}
"""
_PARSER_EXPECTED = {"Fixture::getEmail", "Fixture::normalise"}


class ParserBlind(RuntimeError):
    """lizard cannot see functions it must see; function counts would be wrong."""


def check_parser() -> None:
    """Fail closed if the installed lizard is blind to annotated Java classes."""
    import lizard
    got = {f.name for f in lizard.analyze_file.analyze_source_code(
        _PARSER_FIXTURE_PATH, _PARSER_FIXTURE).function_list}
    if not _PARSER_EXPECTED <= got:
        raise ParserBlind(
            f"lizard sees {sorted(got)} in an @Entity/@Table class, expected "
            f"{sorted(_PARSER_EXPECTED)} (the lizard 1.24 Java regression). "
            f"Install the pinned parser: pip install 'lizard=={LIZARD_VERSION}'")


def _git(args: list[str], cwd: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def source_files(root: str) -> list[str]:
    """Repo-relative paths of the project's own maintained source files: tests, dependency,
    build and generated trees, and oversized blobs excluded."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() not in SOURCE_EXTS:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            if _TEST_DIR.search("/" + rel) or _TEST_FILE.search("/" + rel):
                continue
            try:
                if os.path.getsize(os.path.join(dirpath, fn)) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(rel)
    return out


def file_language(rel: str, h_lang: str) -> str:
    ext = os.path.splitext(rel)[1].lower()
    return h_lang if ext == ".h" else EXT_LANG[ext]


def language(files: list[str]) -> tuple[str | None, float, str]:
    """(dominant language, its share of the files, the `.h` resolution). Ties break by
    language name so re-runs are stable."""
    if not files:
        return None, 0.0, "c"
    exts = Counter(os.path.splitext(f)[1].lower() for f in files)
    h_lang = "c++" if any(exts.get(e) for e in _CXX_EXTS) else "c"
    by_lang = Counter(file_language(f, h_lang) for f in files)
    lang = max(sorted(by_lang), key=by_lang.get)
    return lang, round(by_lang[lang] / len(files), 4), h_lang


def functions(root: str, files: list[str]) -> dict:
    """lizard function and line counts over `files`."""
    import lizard
    n_funcs = n_with = nloc = 0
    for rel in files:
        try:
            info = lizard.analyze_file(os.path.join(root, rel))
        except Exception:
            continue
        n = len(info.function_list)
        n_funcs += n
        n_with += 1 if n else 0
        nloc += info.nloc or 0
    return {"n_functions": n_funcs, "n_files_with_functions": n_with, "nloc": nloc}


def history_counts(root: str) -> dict:
    """History reachable from HEAD (the pinned commit)."""
    n = _git(["rev-list", "--count", "HEAD"], root).stdout.strip()
    emails = _git(["log", "--format=%aE", "HEAD"], root).stdout.split()
    first = _git(["log", "--reverse", "--format=%cI", "HEAD"], root).stdout.split("\n", 1)[0]
    return {"n_commits": int(n) if n.isdigit() else 0,
            "n_contributors": len({e.lower() for e in emails}),
            "first_commit_date": first.strip() or None}


def clone_flags(history: bool) -> list[str]:
    """The `git clone` flags a candidate is cloned with: shallow, or full history (blobless)."""
    return ["--filter=blob:none", "--no-tags"] if history else ["--depth", "1", "--no-tags"]


def clone(url: str, dest: str, history: bool = False,
          timeout: float = CLONE_TIMEOUT) -> str | None:
    """Clone the default branch into `dest`: shallow, or with its full history (blobless).
    None on success, else an error."""
    err = network_git(["clone", *clone_flags(history), "--quiet", url, dest], timeout=timeout,
                      prepare=lambda: shutil.rmtree(dest, ignore_errors=True))
    return None if err is None else f"clone failed: {err}"


def measure(url: str, dest: str, history: bool = False, timeout: float = CLONE_TIMEOUT) -> dict:
    """Clone `url` into `dest` and measure it at the commit its default branch is on now. That
    commit is the pin: it is the code that was checked against the criteria. The history
    measures are taken only with `history` (they need the full history)."""
    err = clone(url, dest, history, timeout)
    if err:
        return {"error": err}
    commit = _git(["rev-parse", "HEAD"], dest).stdout.strip()
    files = source_files(dest)
    lang, share, _ = language(files)
    return {"commit": commit,
            "commit_date": _git(["show", "-s", "--format=%cI", "HEAD"], dest).stdout.strip(),
            "language": lang, "language_share": share, "n_source_files": len(files),
            **functions(dest, files), **(history_counts(dest) if history else {})}
