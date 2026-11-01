"""--as-of: sampling repositories as they were before a date (docs/proposals/as-of.md)."""
import os
import subprocess

import pytest

from open_source_repository_sampler import checkout, cli, files, frame, measure, sample
from test_sampler import PARAMS, _java, draw, population_search, remotes, rows_of  # noqa: F401 (remotes: a fixture)

AS_OF = "2020-12-01"


def _commit(path, when, message, **extra):
    env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a",
           "GIT_COMMITTER_EMAIL": "a@x", "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", *extra.get("args", ["commit", "--quiet", "-m", message])], cwd=path,
                   check=True, capture_output=True, env=env)


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True,
                          text=True).stdout.strip()


def _add(path, rel, text):
    full = os.path.join(path, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(text)
    _git(path, "add", "-A")


def dated_repo(root, name):
    """main: A (2019) with 2 classes, B (2020-06) with 3; a side branch S (2020-11-20, before
    the date) merged as M (2021-01-10, after); then C (2021-02) with 5 classes. As of
    2020-12-01 the newest first-parent commit is B: S is before the date, but not first-parent."""
    path = os.path.join(root, "remote", name)
    os.makedirs(path)
    _git(path, "init", "--quiet", "-b", "main")
    for i in range(2):
        _add(path, f"src/A{i}.java", _java(10))
    _commit(path, "2019-01-01T12:00:00Z", "A")
    _add(path, "src/B.java", _java(10))
    _commit(path, "2020-06-01T12:00:00Z", "B")
    b = _git(path, "rev-parse", "HEAD")
    _git(path, "checkout", "--quiet", "-b", "side")
    _add(path, "src/S.java", _java(10))
    _commit(path, "2020-11-20T12:00:00Z", "S")
    _git(path, "checkout", "--quiet", "main")
    _commit(path, "2021-01-10T12:00:00Z", "M", args=["merge", "--quiet", "--no-ff", "-m", "M", "side"])
    _add(path, "src/C.java", _java(10))
    _commit(path, "2021-02-01T12:00:00Z", "C")
    return "file://" + path, b, _git(path, "rev-parse", "HEAD")


def young_repo(root, name):
    """Every commit after the date."""
    path = os.path.join(root, "remote", name)
    os.makedirs(path)
    _git(path, "init", "--quiet", "-b", "main")
    for i in range(3):
        _add(path, f"src/Y{i}.java", _java(10))
    _commit(path, "2021-03-01T12:00:00Z", "Y")
    return "file://" + path


# --- the frame ---------------------------------------------------------------------------------

def test_the_frame_is_the_repositories_created_before_the_date():
    pop = [("o/old", 300, "2015-05-05"), ("o/eve", 300, "2020-11-30"), ("o/day", 300, "2020-12-01"),
           ("o/new", 300, "2023-01-01")]
    search = population_search(pop)
    s = frame.stratum("java", "low", 1, set(), 0.0, search, as_of=AS_OF)
    assert sorted(c["full_name"] for c in s["order"]) == ["o/eve", "o/old"]
    assert all(" created:2007-10-01..2020-11-30 " in q + " " for q, _, _ in search.calls)
    # without a date, nothing about creation is asked
    plain = population_search(pop)
    frame.stratum("java", "low", 1, set(), 0.0, plain)
    assert all("created:" not in q for q, _, _ in plain.calls)


# --- the pin -----------------------------------------------------------------------------------

def test_the_pin_is_the_newest_first_parent_commit_before_the_date(tmp_path):
    url, b, head = dated_repo(tmp_path, "dated")
    m = measure.measure(url, str(tmp_path / "clone"), as_of=AS_OF)
    assert m["commit"] == b and m["head_commit"] == head
    assert m["commit_date"].startswith("2020-06-01")
    assert m["n_source_files"] == 3            # B's three classes, not the tip's five, nor S


def test_a_repository_with_no_commit_before_the_date_is_excluded(tmp_path):
    remotes = {"o/young": young_repo(tmp_path, "young")}
    for name in ("old1", "old2"):
        remotes[f"o/{name}"], _, _ = dated_repo(tmp_path, name)
    d = draw(tmp_path / "s", remotes, {**PARAMS, "as_of": AS_OF})
    by = {r["full_name"]: r for r in rows_of(tmp_path / "s")}
    assert by["o/young"]["status"] == "excluded"
    assert by["o/young"]["reason"] == f"no commit before as-of {AS_OF}"
    assert by["o/young"]["head_commit"] and not by["o/young"]["commit"]
    assert sorted(n for n, r in by.items() if r["status"] == "usable") == ["o/old1", "o/old2"]
    assert d["parameters"]["as_of"] == AS_OF
    assert f"--as-of {AS_OF}" in d["resolved_command"]
    assert d["sampling_clone"]["git_clone"] == "git clone --filter=tree:0 --no-checkout --no-tags <url> <dir>"
    assert d["method"]["pin"].startswith(f"as of {AS_OF}")
    assert "as_of_limits" in d["method"]


def test_the_csv_names_the_tip_beside_the_pin(tmp_path):
    url, b, head = dated_repo(tmp_path, "dated")
    url2, _, _ = dated_repo(tmp_path, "dated2")
    draw(tmp_path / "s", {"o/dated": url, "o/dated2": url2}, {**PARAMS, "as_of": AS_OF})
    with open(tmp_path / "s" / files.names()[0]) as fh:
        header = fh.readline().strip().split(",")
    assert header == files.columns(AS_OF)
    assert header[header.index("commit_date") + 1] == "head_commit"
    row = {r["full_name"]: r for r in rows_of(tmp_path / "s")}["o/dated"]
    assert (row["commit"], row["head_commit"]) == (b, head)


# --- unchanged without it ----------------------------------------------------------------------

def test_without_a_date_the_sample_is_as_before(tmp_path, remotes):
    d = draw(tmp_path / "s", remotes)
    with open(tmp_path / "s" / files.names()[0]) as fh:
        assert fh.readline().strip().split(",") == files.COLUMNS
    assert "--as-of" not in d["resolved_command"]
    assert "as_of_limits" not in d["method"]
    assert d["sampling_clone"]["mode"] == "shallow"


def test_a_checkpoint_from_before_as_of_still_resumes(tmp_path, remotes):
    out = tmp_path / "s"
    os.makedirs(out)
    prog = sample.Progress(str(out / files.names()[2]))
    old = {k: v for k, v in PARAMS.items() if k not in ("pause", "max_clones", "as_of")}
    from open_source_repository_sampler import provenance
    prog.add("parameters", {"parameters": old, "started_utc": "2026-01-01T00:00:00Z",
                            "sampler": provenance.sampler()})
    draw(out, remotes)                         # no "different parameters" refusal
    assert len(rows_of(out)) >= 2


# --- verify ------------------------------------------------------------------------------------

def test_verify_rederives_the_as_of_commit(tmp_path):
    url, b, _ = dated_repo(tmp_path, "dated")
    url2, _, _ = dated_repo(tmp_path, "dated2")
    draw(tmp_path / "s", {"o/dated": url, "o/dated2": url2}, {**PARAMS, "as_of": AS_OF})
    assert checkout.verify(str(tmp_path / "s"), fetch=True, log=lambda *_: None) == []
    # history before the date rewritten: B amended, so the pinned commit is no longer the newest
    path = url[len("file://"):]
    _git(path, "checkout", "--quiet", b)
    _add(path, "src/B2.java", _java(10))
    _commit(path, "2020-07-01T12:00:00Z", "B2")
    _git(path, "branch", "-f", "main", "HEAD")
    _git(path, "checkout", "--quiet", "main")
    problems = checkout.verify(str(tmp_path / "s"), fetch=True, log=lambda *_: None)
    assert any("o/dated:" in p and f"before {AS_OF}" in p for p in problems), problems


# --- the command line --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["2020-13-01", "yesterday", "2999-01-01"])
def test_the_command_refuses_a_bad_or_future_date(bad, capsys):
    with pytest.raises(SystemExit):
        cli.main(["sample", "--as-of", bad, "--out-dir", "/nonexistent"])
    assert "--as-of" in capsys.readouterr().err
