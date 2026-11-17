"""Declared agent commits: the signatures, the counts over a history, and the criterion."""
import os
import subprocess

import pytest

from open_source_repository_sampler import agents, sample

from test_sampler import PARAMS, _java, draw, remotes, rows_of  # noqa: F401 (remotes is a fixture)


def commit(path, message, author="a <a@x>", committer=None):
    name, email = author[:-1].split(" <")
    cname, cemail = (committer or author)[:-1].split(" <")
    with open(os.path.join(path, "f.txt"), "a") as fh:
        fh.write(message + "\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": cname, "GIT_COMMITTER_EMAIL": cemail}
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=path, check=True,
                   capture_output=True, env=env)


@pytest.mark.parametrize("author, committer, message, expected", [
    ("a <a@x>", "a <a@x>", "fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>", ["claude"]),
    ("a <a@x>", "a <a@x>", "fix\n\nco-authored-by: Claude Opus 4 <noreply@anthropic.com>", ["claude"]),
    ("a <a@x>", "a <a@x>", "fix\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)", ["claude"]),
    ("copilot-swe-agent[bot] <198982749+Copilot@users.noreply.github.com>", "GitHub <noreply@github.com>",
     "Add check", ["copilot"]),
    ("a <a@x>", "a <a@x>", "x\n\nCo-authored-by: Copilot <175728472+Copilot@users.noreply.github.com>", ["copilot"]),
    ("Cursor Agent <cursoragent@cursor.com>", "a <a@x>", "x", ["cursor"]),
    ("devin-ai-integration[bot] <158243242+devin-ai-integration[bot]@users.noreply.github.com>", "a <a@x>",
     "x", ["devin"]),
    ("a <a@x>", "a <a@x>", "x\n\nCo-authored-by: Codex <noreply@openai.com>", ["codex"]),
    ("google-labs-jules[bot] <161369871+google-labs-jules[bot]@users.noreply.github.com>", "a <a@x>", "x",
     ["jules"]),
    ("Ann (aider) <ann@x>", "Ann (aider) <ann@x>", "x", ["aider"]),
    ("a <a@x>", "a <a@x>", "x\n\nCo-authored-by: aider (gpt-4o) <aider@aider.chat>", ["aider"]),
    ("openhands <openhands@all-hands.dev>", "a <a@x>", "x", ["openhands"]),
    # two agents on one commit count for both
    ("Cursor Agent <cursoragent@cursor.com>", "a <a@x>",
     "x\n\nCo-Authored-By: Claude <noreply@anthropic.com>", ["claude", "cursor"]),
])
def test_a_commit_declares_its_agent(author, committer, message, expected):
    assert agents.declared(author, committer, message) == expected


@pytest.mark.parametrize("author, message", [
    ("a <a@x>", "Use Claude to summarise the report"),                 # prose is not a declaration
    ("a <a@x>", "x\n\nCo-authored-by: Bea <bea@x>"),                    # a person as co-author
    ("dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>", "Bump x"),   # a bot, not an agent
    ("Claude Monet <claude@x>", "paint"),                              # a person named Claude
    ("a <a@x>", "x\n\nSee noreply@anthropic.com for details"),          # the address outside a trailer
])
def test_what_is_not_a_declaration(author, message):
    assert agents.declared(author, author, message) == []


def test_counts_over_a_history(tmp_path):
    path = str(tmp_path)
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=path, check=True)
    commit(path, "c0")
    commit(path, "c1\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    commit(path, "c2", author="copilot-swe-agent[bot] <1+Copilot@users.noreply.github.com>")
    commit(path, "c3\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    got = agents.counts(path)
    assert got["n_agent_commits"] == 3 and got["agents"] == "claude:2;copilot:1"
    assert got["first_agent_commit_date"]
    empty = tmp_path / "e"
    empty.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=empty, check=True)
    commit(str(empty), "only people")
    assert agents.counts(str(empty)) == {"n_agent_commits": 0, "first_agent_commit_date": None,
                                         "agents": ""}


def test_min_agent_commits_is_a_history_criterion(tmp_path):
    def repo(name, declared):
        path = os.path.join(tmp_path, "remote", name)
        os.makedirs(os.path.join(path, "src"))
        subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=path, check=True)
        for i in range(4):
            with open(os.path.join(path, "src", f"C{i}.java"), "w") as fh:
                fh.write(_java(10))
        commit(path, "base")
        for i in range(declared):
            commit(path, f"a{i}\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        return "file://" + path
    remotes = {"o/agentic": repo("agentic", 3), "o/plain": repo("plain", 0), "o/some": repo("some", 1)}
    d = draw(tmp_path / "s", remotes, params={**PARAMS, "target": 3, "min_agent_commits": 2})
    by = {r["full_name"]: r for r in rows_of(tmp_path / "s")}
    assert d["sampling_clone"]["mode"] == "full-history"
    assert "min_agent_commits = 2" in d["sampling_clone"]["because"]
    assert (by["o/agentic"]["status"], by["o/agentic"]["n_agent_commits"]) == ("usable", "3")
    assert by["o/agentic"]["agents"] == "claude:3"
    assert by["o/plain"]["status"] == "excluded" and "n_agent_commits 0 < 2" in by["o/plain"]["reason"]
    assert by["o/some"]["status"] == "excluded"
    # the derivation says what was counted
    sig = d["sampling_clone"]["agent_signatures"]
    assert sig["version"] == agents.SIGNATURES_VERSION
    assert {"agent": "claude", "where": "trailer", "pattern": r"<noreply@anthropic\.com>",
            "basis": "documented"} in sig["signatures"]


def test_a_shallow_sample_measures_no_agent_commits(tmp_path, remotes):
    d = draw(tmp_path / "s", remotes)
    assert "agent_signatures" not in d["sampling_clone"]
    assert {r["n_agent_commits"] for r in rows_of(tmp_path / "s")} == {""}
