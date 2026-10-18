"""Which exact version of the sampler produced a sample, and with which tools.

A sample is only reproducible if the code that drew it is pinned as well as the repositories
it drew. So every sample records the sampler's own commit. It is read, in order of trust:

  1. a live git checkout of the sampler (a clone, or an editable install): `git rev-parse HEAD`
     and whether the working tree has uncommitted changes, with a hash of those changes;
  2. the commit stamped into `_version.py` by setuptools-scm when the package was built (a
     pip install from PyPI or from git, where no .git sits beside the code).

A dirty sampler is refused by default: its commit would not describe the code that ran.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess

NAME = "open-source-repository-sampler"
REPOSITORY = "https://github.com/officefloor/open-source-repository-sampler"
PYPI = "https://pypi.org/project/open-source-repository-sampler"


def about(sampler: dict) -> dict:
    """Where a reader of a sample's files finds the tool that made them, its documentation,
    and the exact source that ran: for someone who meets the files in a paper's ancillary data
    with no other context."""
    commit, version = sampler.get("commit"), sampler.get("version")
    out = {
        "what": (f"These two files ({{csv}} and this derivation) are a repository sample made by "
                 f"{NAME}: a seeded, stratified sample of open-source repositories, each pinned "
                 f"to the commit that met the sampling criteria. This file records how the "
                 f"sample was derived and how to reproduce it."),
        "tool": NAME,
        "repository": REPOSITORY,
        "documentation": f"{REPOSITORY}#readme",
        "pypi": PYPI,
    }
    if commit:
        out["source_at_this_commit"] = f"{REPOSITORY}/tree/{commit}"
        out["documentation_at_this_commit"] = f"{REPOSITORY}/blob/{commit}/README.md"
    if version and not sampler.get("dirty") and "dev" not in version and "+" not in version:
        out["pypi_release"] = f"{PYPI}/{version}/"
        out["install"] = f"pip install {NAME}=={version}"
    elif commit:
        out["install"] = f"pip install 'git+{REPOSITORY}.git@{commit}'"
    return out

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _checkout_root() -> str | None:
    """The sampler's own git checkout, if this package is running from one. Only accepted when
    the checkout really is this project (it holds our pyproject), so a sampler installed inside
    some other git repository (an experiment's .venv) is not mistaken for it."""
    try:
        r = _git(["rev-parse", "--show-toplevel"], _PKG_DIR)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    root = r.stdout.strip()
    try:
        with open(os.path.join(root, "pyproject.toml")) as fh:
            if f'name = "{NAME}"' not in fh.read():
                return None
    except OSError:
        return None
    return root


def _stamped() -> dict:
    try:
        from . import _version
    except ImportError:
        return {}
    node = getattr(_version, "__commit__", None) or ""
    return {"version": getattr(_version, "version", None),
            "commit": node[1:] if node.startswith("g") else (node or None),
            "dirty": bool(getattr(_version, "__dirty__", False))}


def sampler() -> dict:
    """{name, repository, version, commit, dirty, diff_sha256, source} for the running sampler.
    `commit` is None when it cannot be determined (reported, never guessed)."""
    stamped = _stamped()
    out = {"name": NAME, "repository": REPOSITORY, "version": stamped.get("version"),
           "commit": None, "dirty": None, "diff_sha256": None, "source": None}
    root = _checkout_root()
    if root:
        head = _git(["rev-parse", "HEAD"], root)
        out["source"] = "git checkout"
        out["commit"] = head.stdout.strip() if head.returncode == 0 else None
        # Untracked files count: a new, uncommitted module changes what runs just as an edit does.
        status = _git(["status", "--porcelain", "--untracked-files=normal"], root).stdout
        out["dirty"] = bool(status.strip()) or out["commit"] is None
        if out["dirty"]:
            diff = _git(["diff", "HEAD", "--binary"], root).stdout if out["commit"] else ""
            out["diff_sha256"] = hashlib.sha256((diff + status).encode()).hexdigest()
    elif stamped:
        out["source"] = "build stamp"
        out["commit"] = stamped.get("commit")
        out["dirty"] = stamped.get("dirty") or out["commit"] is None
    else:
        out["dirty"] = True
    return out


def tools() -> dict:
    """Versions of everything else that shapes a sample: a version change in any of these can
    change which repositories meet the criteria, so it is recorded with the sample."""
    v = {"python": platform.python_version()}
    try:
        from importlib.metadata import version
        v["lizard"] = version("lizard")
    except Exception:
        v["lizard"] = None
    for tool, args in (("git", ["git", "--version"]), ("gh", ["gh", "--version"])):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            v[tool] = (r.stdout.splitlines() or [""])[0].strip() or None
        except (OSError, subprocess.TimeoutExpired):
            v[tool] = None
    return v
