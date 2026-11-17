"""Commits an AI coding agent declares as its own.

A commit is a **declared agent commit** when its metadata says an agent wrote or co-wrote it:

  * its author or committer is an agent's account (an identity signature), or
  * its message carries an agent's `Co-authored-by` trailer (a trailer signature), or
  * its message carries the line an agent adds to what it generates (a message signature).

This is what a commit *says*, not who wrote the code. Assisted code committed under a person's
name with no trailer is not counted, and a trailer can be added or removed by hand, so the count
is a lower bound on agent involvement and is not evidence that the rest was written unaided.

Like every measure of the sampler it describes the repository's history, never an outcome a
study tests. The signatures are data, fixed per release (`SIGNATURES_VERSION`), and written
into every derivation that used them, so a sample states exactly what it counted.

Each signature is (agent, where, pattern, basis). `where` is `identity` (matched against the
author's and the committer's "Name <email>"), `trailer` (against the "Name <email>" of each
`Co-authored-by` line) or `message` (against the whole message). Patterns are case-insensitive
regular expressions. `basis` says where the marker comes from: `documented` (the agent's own
documentation or changelog) or `observed` (the identity its commits carry on GitHub).
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter

SIGNATURES_VERSION = 1

SIGNATURES = [
    # Claude Code: the trailer and the line it adds by default (Anthropic's documentation).
    ("claude", "trailer", r"<noreply@anthropic\.com>", "documented"),
    ("claude", "message", r"Generated with \[Claude Code\]", "documented"),
    ("claude", "identity", r"^claude\[bot\] <", "observed"),
    # GitHub Copilot's coding agent commits under its own account, and is named as co-author.
    ("copilot", "identity", r"^copilot-swe-agent\[bot\] <", "observed"),
    ("copilot", "identity", r"\+Copilot@users\.noreply\.github\.com>", "observed"),
    ("copilot", "trailer", r"\+Copilot@users\.noreply\.github\.com>", "observed"),
    # Cursor's agents commit as, or name as co-author, the Cursor agent address.
    ("cursor", "identity", r"<cursoragent@cursor\.com>", "observed"),
    ("cursor", "trailer", r"<cursoragent@cursor\.com>", "observed"),
    # Devin commits under its GitHub App's account.
    ("devin", "identity", r"devin-ai-integration\[bot\]", "observed"),
    ("devin", "trailer", r"devin-ai-integration\[bot\]", "observed"),
    # OpenAI Codex: the default trailer of the Codex CLI since February 2026 (its changelog).
    ("codex", "trailer", r"^Codex <noreply@openai\.com>", "documented"),
    # Jules commits under Google Labs' GitHub App.
    ("jules", "identity", r"google-labs-jules\[bot\]", "observed"),
    ("jules", "trailer", r"google-labs-jules\[bot\]", "observed"),
    # aider appends "(aider)" to the author's and committer's name, and names itself in a
    # trailer (aider's git documentation).
    ("aider", "identity", r"\(aider\) <", "documented"),
    ("aider", "trailer", r"^aider \(.*\) <|<(?:noreply|aider)@aider\.chat>", "documented"),
    # OpenHands commits as the openhands identity.
    ("openhands", "identity", r"<openhands@all-hands\.dev>", "observed"),
    ("openhands", "trailer", r"<openhands@all-hands\.dev>", "observed"),
]

_COMPILED = [(agent, where, re.compile(pattern, re.I)) for agent, where, pattern, _ in SIGNATURES]
_TRAILER = re.compile(r"^co-authored-by:\s*(.+?)\s*$", re.I | re.M)
_FIELD, _RECORD = "\x1f", "\x1e"


def declared(author: str, committer: str, message: str) -> list[str]:
    """The agents a commit declares, sorted; empty when it declares none. `author` and
    `committer` are "Name <email>"."""
    trailers = _TRAILER.findall(message)
    found = set()
    for agent, where, rx in _COMPILED:
        if where == "identity":
            hit = rx.search(author) or rx.search(committer)
        elif where == "trailer":
            hit = any(rx.search(t) for t in trailers)
        else:
            hit = rx.search(message)
        if hit:
            found.add(agent)
    return sorted(found)


def counts(root: str) -> dict:
    """Over the history reachable from HEAD (the pinned commit): the declared agent commits,
    the committer date of the earliest, and how many each agent declares (`agents`, as
    "name:count" joined by ";", most first; a commit declaring two agents counts for both)."""
    out = subprocess.run(
        ["git", "log", f"--format=%aN <%aE>{_FIELD}%cN <%cE>{_FIELD}%cI{_FIELD}%B{_RECORD}", "HEAD"],
        cwd=root, capture_output=True, text=True, errors="replace").stdout
    n, first, per = 0, None, Counter()
    for record in out.split(_RECORD):
        parts = record.lstrip("\n").split(_FIELD, 3)
        if len(parts) < 4:
            continue
        author, committer, when, message = parts
        agents = declared(author, committer, message)
        if agents:
            n += 1
            per.update(agents)
            first = when if first is None or when < first else first
    return {"n_agent_commits": n, "first_agent_commit_date": first,
            "agents": ";".join(f"{a}:{c}" for a, c in sorted(per.items(), key=lambda x: (-x[1], x[0])))}


def describe() -> dict:
    """The signatures as a derivation records them."""
    return {"version": SIGNATURES_VERSION,
            "what": ("a declared agent commit has an author or committer matching an identity "
                     "signature, a Co-authored-by trailer matching a trailer signature, or a "
                     "message matching a message signature (case-insensitive regular "
                     "expressions). It counts what commits say: assisted code committed with no "
                     "such marker is not counted, so the count is a lower bound."),
            "signatures": [{"agent": a, "where": w, "pattern": p, "basis": b}
                           for a, w, p, b in SIGNATURES]}
