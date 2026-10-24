"""Behaviour tests for the sampler. Each pins one property a sample's reproducibility relies on.

No network: GitHub search is a stub, and the candidates are real local git repositories, so
cloning, pinning, language detection and measurement all run for real.
"""
import csv
import json
import os
import re
import subprocess
import sys

import pytest

from open_source_repository_sampler import checkout, files, frame, measure, sample


# --- fixtures ---------------------------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x",
                        "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x"})


def _java(n):
    body = "\n".join(f"    int m{i}(int x) {{ return x + {i}; }}" for i in range(n))
    return f"public class C {{\n{body}\n}}\n"


def make_repo(root, name, sources, commits=1):
    path = os.path.join(root, "remote", name)
    os.makedirs(path)
    _git(path, "init", "--quiet", "-b", "main")
    for i in range(commits):
        for rel, text in sources.items():
            full = os.path.join(path, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text + f"// {i}\n")
        _git(path, "add", "-A")
        _git(path, "commit", "--quiet", "-m", f"c{i}")
    return "file://" + path


@pytest.fixture
def remotes(tmp_path):
    """Four Java candidates: two substantial, one tiny, one actually Kotlin."""
    big = {f"src/C{i}.java": _java(10) for i in range(4)}
    return {
        "o/big1": make_repo(tmp_path, "big1", big, commits=3),
        "o/big2": make_repo(tmp_path, "big2", big, commits=2),
        "o/tiny": make_repo(tmp_path, "tiny", {"src/A.java": _java(1)}),
        "o/kt": make_repo(tmp_path, "kt", {f"src/K{i}.kt": "fun f() = 1\n" for i in range(5)}),
        "me/own": make_repo(tmp_path, "own", big),
    }


def _range(q):
    """(stars lo, stars hi, created lo, created hi) a stub reads off a query."""
    m = re.search(r"stars:(?:(\d+)\.\.(\d+)|>=(\d+))", q)
    lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), 10 ** 9)
    c = re.search(r"created:(\S+)\.\.(\S+)", q)
    return lo, hi, (c.group(1) if c else "0000"), (c.group(2) if c else "9999")


def population_search(repos):
    """A GitHub search stub over a population of (full_name, stars, created) tuples, with
    GitHub's paging and stars-descending order. Records every call in `search.calls`."""
    calls = []

    def search(q, per_page, page):
        calls.append((q, per_page, page))
        lo, hi, c_lo, c_hi = _range(q)
        match = sorted((r for r in repos if lo <= r[1] <= hi and c_lo <= r[2] <= c_hi),
                       key=lambda r: (-r[1], r[0]))
        start = (page - 1) * per_page
        return {"total_count": len(match),
                "items": [{"full_name": n, "clone_url": f"file:///{n}", "language": "Java",
                           "stargazers_count": s} for n, s, _ in match[start:start + per_page]]}
    search.calls = calls
    return search


def stub_search(remotes, stars=300):
    """A GitHub search stub: every candidate has `stars` stars (the `low` tier)."""
    def search(q, per_page, page):
        lo, hi, _, _ = _range(q)
        items = [{"full_name": n, "clone_url": u, "language": "Java", "stargazers_count": stars}
                 for n, u in remotes.items()] if lo <= stars <= hi else []
        start = (page - 1) * per_page
        return {"total_count": len(items), "items": items[start:start + per_page]}
    return search


PARAMS = {**sample.DEFAULTS, "langs": ["java"], "tiers": ["low"], "target": 2,
          "exclude_owners": ["me"], "min_functions": 5, "min_files_with_functions": 2,
          "pause": 0.0}
# The same sample with a history criterion, so candidates are cloned with their full history.
HISTORY = {**PARAMS, "min_commits": 2}


def draw(out_dir, remotes, params=PARAMS, **kw):
    search = kw.pop("search", None) or stub_search(remotes)
    return sample.run(dict(params), str(out_dir), kw.pop("label", None), ["sample"],
                      allow_dirty=True, search=search, log=lambda *_: None, **kw)


def rows_of(out_dir, label=None):
    with open(os.path.join(out_dir, files.names(label)[0]), newline="") as fh:
        return list(csv.DictReader(fh))


# --- the frame and the draw order -------------------------------------------------------------

def _bands(n):
    return [{"band": b, "total_matched": 10 * (i + 1),
             "candidates": [{"full_name": f"{b}/{j}"} for j in range(n)]}
            for i, b in enumerate(["a", "b", "c"])]


def test_draw_order_is_fixed_by_the_seed_alone():
    one = [c["full_name"] for c in frame.draw_order(_bands(20), "1:java:low")]
    again = [c["full_name"] for c in frame.draw_order(_bands(20), "1:java:low")]
    other = [c["full_name"] for c in frame.draw_order(_bands(20), "2:java:low")]
    assert one == again
    assert one != other
    assert sorted(one) == sorted(f"{b}/{j}" for b in "abc" for j in range(20))
    # The order depends on the set of candidates, not on the order GitHub returned them in.
    reversed_bands = [{**b, "candidates": b["candidates"][::-1]} for b in _bands(20)[::-1]]
    assert [c["full_name"] for c in frame.draw_order(reversed_bands, "1:java:low")] == one


def test_query_excludes_owners_and_forks():
    q = frame.query("c++", {"stars": (200, 290)}, {"me", "you"})
    assert q == "language:C++ stars:200..290 archived:false fork:false -user:me -user:you"
    q = frame.query("c", {"stars": (5001, None)}, set())
    assert q == "language:C stars:>=5001 archived:false fork:false"
    q = frame.query("c", {"stars": (200, 200), "created": ("2010-01-01", "2012-06-30")}, set())
    assert q.startswith("language:C stars:200..200 created:2010-01-01..2012-06-30 ")


def test_bands_split_on_stars_then_on_creation_date():
    assert frame.split({"stars": (200, 999)}) == ({"stars": (200, 599)}, {"stars": (600, 999)})
    assert frame.split({"stars": (5001, None)}) == ({"stars": (5001, 10002)},
                                                    {"stars": (10003, None)})
    a, b = frame.split({"stars": (200, 200)})
    assert a["stars"] == b["stars"] == (200, 200)
    assert a["created"][0] == "2007-10-01" and a["created"][1] < b["created"][0]
    assert frame.split({"stars": (200, 200), "created": ("2015-01-01", "2015-01-01")}) is None


def _population(n, stars, created=lambda i: f"{2010 + i % 10}-{1 + i % 12:02d}-{1 + i % 28:02d}"):
    return [(f"o/r{i}", stars(i), created(i)) for i in range(n)]


def test_the_frame_is_the_whole_population_fetched_in_full_pages():
    repos = _population(2500, lambda i: 200 + i % 800)        # far more than GitHub's 1000 cap
    search = population_search(repos)
    s = frame.stratum("java", "low", 1, set(), 0.0, search)
    assert sorted(c["full_name"] for c in s["order"]) == sorted(n for n, _, _ in repos)
    assert s["population"] == 2500 and len(s["bands"]) >= 3
    assert all(b["total_matched"] <= frame.SEARCH_CAP and not b["truncated"] for b in s["bands"])
    pages = [(q, pp, pg) for q, pp, pg in search.calls if pp != 1]
    assert pages and all(pp == frame.PAGE for _, pp, _ in pages)   # pages never overlap
    assert len({c["full_name"] for c in s["order"]}) == len(s["order"])


def test_a_band_is_fetched_page_by_page_without_overlap():
    search = population_search(_population(250, lambda i: 200 + i))
    s = frame.stratum("java", "low", 1, set(), 0.0, search)
    assert len(s["bands"]) == 1 and len(s["order"]) == 250
    # 250 results are pages 1, 2 and 3 of 100 (the last one short), and never a fourth.
    assert [(pp, pg) for _, pp, pg in search.calls if pp != 1] == [(100, 1), (100, 2), (100, 3)]


def test_a_single_star_value_over_the_cap_is_split_on_creation_date():
    repos = _population(1500, lambda i: 200)
    s = frame.stratum("java", "low", 1, set(), 0.0, population_search(repos))
    assert len(s["order"]) == 1500
    assert all(not b["truncated"] for b in s["bands"])
    assert any("created:" in b["query"] for b in s["bands"])


def test_a_band_that_cannot_be_split_is_fetched_to_the_cap_and_marked_truncated():
    repos = _population(1200, lambda i: 200, created=lambda i: "2015-06-01")
    said = []
    s = frame.stratum("java", "low", 1, set(), 0.0, population_search(repos), log=said.append)
    trunc = [b for b in s["bands"] if b["truncated"]]
    assert len(trunc) == 1 and trunc[0]["total_matched"] == 1200
    assert len(s["order"]) == frame.SEARCH_CAP
    assert any("TRUNCATED" in w.upper() for w in said)


def test_a_failed_search_stops_the_run_instead_of_freezing_a_gap(tmp_path, remotes):
    good = stub_search(remotes)

    def failing(q, per_page, page):
        return {"total_count": 0, "items": [], "error": "boom"}

    with pytest.raises(frame.FrameError, match="boom"):
        draw(tmp_path / "s", remotes, search=failing)
    progress = (tmp_path / "s" / files.names()[2]).read_text()
    assert '"type": "stratum"' not in progress                  # nothing frozen for it
    assert not os.path.exists(tmp_path / "s" / files.names()[0])
    d = draw(tmp_path / "s", remotes, search=good)             # the re-run enumerates afresh
    assert d["summary"]["usable_per_language"] == {"java": 2}


def test_incomplete_results_are_retried_then_refused(monkeypatch):
    monkeypatch.setattr(frame, "INCOMPLETE_WAIT", 0)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        body = json.dumps({"total_count": 5, "incomplete_results": True, "items": []})
        return subprocess.CompletedProcess(args, 0, body, "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    res = frame.gh_search("q", 100, 1)
    assert len(calls) == frame.INCOMPLETE_RETRIES + 1
    assert "incomplete" in res["error"]
    assert "per_page=100" in calls[0]


# --- language detection and criteria ----------------------------------------------------------

def test_headers_do_not_vote_between_c_and_cxx():
    assert measure.language(["a.h", "b.h", "c.cpp"])[0] == "c++"
    assert measure.language(["a.h", "b.h", "c.c"])[0] == "c"
    # A C project with one C++ binding and many headers is still C.
    lang, share, h = measure.language(["a.h", "b.h", "c.h", "d.h", "x.cpp", "y.c", "z.c"])
    assert (lang, share, h) == ("c", 0.6667, "c++")
    # Headers only: GitHub's label decides between C and C++, and nothing else.
    assert measure.language(["a.h", "b.h"], hint="C++")[0] == "c++"
    assert measure.language(["a.h", "b.h"], hint="Java")[0] == "c"
    lang, share, _ = measure.language(["a.java", "b.java", "c.kt", "d.java"])
    assert (lang, share) == ("java", 0.75)


def test_tests_vendored_and_generated_code_are_not_the_projects_source(tmp_path):
    for rel in ["src/A.java", "src/test/java/ATest.java", "vendor/x.go", "pkg/vendor/y.go",
                "pkg/a_test.go", "web/a.spec.ts", "node_modules/m/i.js", "lib/b.py",
                "tests/t.py", "build/B.java", "target/classes/T.java", "web/app.min.js",
                "proto/msg_pb2.py", "proto/msg.pb.go", "types/index.d.ts",
                "src/main/java/org/x/target/Hot.java",      # a package named `target`
                "src/main/java/org/x/build/Builder.java"]:  # a package named `build`
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    assert measure.source_files(str(tmp_path)) == [
        "lib/b.py", "src/A.java", "src/main/java/org/x/build/Builder.java",
        "src/main/java/org/x/target/Hot.java"]


def test_judge_names_every_unmet_criterion():
    p = {**PARAMS, "min_commits": 10}
    row = {"language": "java", "n_functions": 3, "n_files_with_functions": 9, "n_commits": 4}
    status, reason = sample.judge(row, p)
    assert status == "excluded"
    assert reason == "n_functions 3 < 5; n_commits 4 < 10"
    assert sample.judge({"language": "kotlin"}, p) == ("excluded", "language kotlin not sampled")
    # Loss to infrastructure is kept apart from exclusion by the criteria.
    assert sample.judge({"error": "clone failed"}, p) == ("error", "clone failed")


def test_a_transient_network_failure_is_retried_and_a_permanent_one_is_not(tmp_path,
                                                                          monkeypatch):
    monkeypatch.setattr(measure, "NETWORK_RETRY_WAIT", 0)
    tries = []
    err = measure.network_git(["ls-remote", "file:///no/such/repo"],
                              prepare=lambda: tries.append(1))
    assert err and len(tries) == 1                               # permanent: not retried
    real = subprocess.run
    calls = []

    def flaky(args, **kw):
        calls.append(args)
        if len(calls) < 3:
            return subprocess.CompletedProcess(args, 128, "", "fatal: early EOF")
        return real(["git", "--version"], **kw)
    monkeypatch.setattr(subprocess, "run", flaky)
    assert measure.network_git(["fetch", "x"]) is None            # third attempt succeeds
    assert len(calls) == 3
    assert "http.lowSpeedLimit=1000" in calls[0] and "http.lowSpeedTime=60" in calls[0]


def test_parser_is_not_blind():
    measure.check_parser()


# --- a whole sample ---------------------------------------------------------------------------

def test_sample_pins_commits_and_records_every_candidate(tmp_path, remotes):
    d = draw(tmp_path / "s", remotes)
    rows = rows_of(tmp_path / "s")
    assert rows[0].keys() == set(files.COLUMNS)
    by = {r["full_name"]: r for r in rows}
    assert "me/own" not in by                                    # excluded owner never drawn
    # Two usable reached the target; whatever else was drawn is kept, with its reason.
    assert sorted(n for n, r in by.items() if r["status"] == "usable") == ["o/big1", "o/big2"]
    for n, r in by.items():
        remote_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                     cwd=remotes[n][len("file://"):]).stdout.strip()
        assert r["commit"] == remote_head
    if "o/kt" in by:
        assert by["o/kt"]["reason"] == "language kotlin not sampled"
    assert by["o/big1"]["n_functions"] == "40"
    assert by["o/big1"]["n_unparsed_files"] == "0" and by["o/big1"]["mirror"] == "False"
    assert by["o/big1"]["n_commits"] == ""                       # shallow: history not measured
    assert d["method"]["clone"].startswith("shallow")
    assert d["sampling_clone"]["mode"] == "shallow"
    assert d["sampling_clone"]["git_clone"] == "git clone --depth 1 --no-tags <url> <dir>"
    assert d["sampling_clone"]["history_columns_measured"] is False
    assert d["summary"]["usable_per_language"] == {"java": 2}
    strat = d["summary"]["per_stratum"]["java/low"]
    assert strat["population"] == 5 and strat["usable"] == 2 and strat["error"] == 0
    assert strat["drawn"] == len(rows) and d["summary"]["truncated_bands"] == []
    assert d["csv_sha256"] == files.sha256(tmp_path / "s" / files.names()[0])
    assert not os.path.exists(tmp_path / "s" / files.names()[2])  # checkpoint removed


def test_a_language_stops_at_its_target_exactly(tmp_path, remotes):
    d = draw(tmp_path / "s", remotes, params={**PARAMS, "target": 1})
    assert d["summary"]["usable"] == 1
    assert sum(1 for r in rows_of(tmp_path / "s") if r["status"] == "usable") == 1


def test_a_candidate_that_cannot_be_cloned_is_an_error_not_an_exclusion(tmp_path, remotes):
    bad = {**remotes, "o/gone": "file:///no/such/repo"}
    d = draw(tmp_path / "s", bad, params={**PARAMS, "target": 4})   # must draw everything
    by = {r["full_name"]: r for r in rows_of(tmp_path / "s")}
    assert by["o/gone"]["status"] == "error" and "clone failed" in by["o/gone"]["reason"]
    assert by["o/tiny"]["status"] == "excluded"
    assert d["summary"]["error"] == 1 and d["summary"]["per_stratum"]["java/low"]["error"] == 1
    assert sorted(r["full_name"] for r in files.read_sample(str(tmp_path / "s"))) == \
        ["o/big1", "o/big2"]                                      # errors are not usable


def test_history_is_cloned_and_measured_only_when_a_criterion_needs_it(tmp_path, remotes):
    d = draw(tmp_path / "s", remotes, params=HISTORY)
    by = {r["full_name"]: r for r in rows_of(tmp_path / "s")}
    assert d["method"]["clone"].startswith("full history")
    assert d["sampling_clone"]["mode"] == "full-history"
    assert d["sampling_clone"]["because"] == ["min_commits = 2"]
    assert d["sampling_clone"]["git_clone"] == "git clone --filter=blob:none --no-tags <url> <dir>"
    assert d["sampling_clone"]["history_columns_measured"] is True
    assert (by["o/big1"]["n_commits"], by["o/big2"]["n_commits"]) == ("3", "2")
    assert by["o/big1"]["n_contributors"] == "1" and by["o/big1"]["first_commit_date"]
    strict = draw(tmp_path / "t", remotes, params={**PARAMS, "min_commits": 3})
    assert strict["summary"]["usable_per_language"] == {"java": 1}   # big2 has only 2 commits


def test_the_clone_cost_is_announced_before_any_download(tmp_path, remotes):
    for params, word in ((PARAMS, "SHALLOW"), (HISTORY, "FULL-HISTORY")):
        said = []
        sample.run(dict(params), str(tmp_path / word), None, ["sample"], allow_dirty=True,
                   search=stub_search(remotes), log=said.append)
        assert word in said[0]
    assert "--min-commits 2" in sample.clone_notice(HISTORY)


def test_same_seed_same_sample(tmp_path, remotes):
    draw(tmp_path / "a", remotes)
    draw(tmp_path / "b", remotes)
    key = lambda rows: [(r["draw"], r["full_name"], r["commit"], r["status"]) for r in rows]
    assert key(rows_of(tmp_path / "a")) == key(rows_of(tmp_path / "b"))


def test_derivation_holds_the_command_and_the_sampler(tmp_path, remotes):
    d = json.loads(open(draw(tmp_path / "s", remotes) and
                        tmp_path / "s" / files.names()[1]).read())
    assert d["derivation_of"] == "open-source-repository-sample.csv"
    assert next(iter(d)) == "about"                              # first thing a reader sees
    assert d["about"]["repository"] == "https://github.com/officefloor/open-source-repository-sampler"
    assert "open-source-repository-sample.csv" in d["about"]["what"]
    assert d["command"] == "open-source-repository-sampler sample"
    assert "--seed 1" in d["resolved_command"] and "--exclude-owners me" in d["resolved_command"]
    assert d["sampler"]["name"] == "open-source-repository-sampler"
    assert "commit" in d["sampler"] and "lizard" in d["sampler"]["tools"]
    assert d["frame"][0]["bands"][0]["query"].startswith("language:Java stars:200..999")
    assert d["frame"][0]["population"] == 5
    assert "allocation" in d["method"] and "status" in d["method"]
    rows = rows_of(tmp_path / "s")
    assert {r["sampler_commit"] for r in rows} == {d["sampler"]["commit"] or ""}


def test_about_points_at_the_exact_release_and_source():
    from open_source_repository_sampler import provenance
    a = provenance.about({"commit": "abc123", "version": "0.1.1", "dirty": False})
    repo = "https://github.com/officefloor/open-source-repository-sampler"
    assert a["source_at_this_commit"] == f"{repo}/tree/abc123"
    assert a["documentation_at_this_commit"] == f"{repo}/blob/abc123/README.md"
    assert a["pypi_release"] == "https://pypi.org/project/open-source-repository-sampler/0.1.1/"
    assert a["install"] == "pip install open-source-repository-sampler==0.1.1"
    dev = provenance.about({"commit": "abc123", "version": "0.1.2.dev3+gabc123", "dirty": False})
    assert "pypi_release" not in dev and dev["install"].endswith(".git@abc123'")


def test_a_sample_is_never_silently_replaced(tmp_path, remotes):
    draw(tmp_path / "s", remotes)
    with pytest.raises(FileExistsError):
        draw(tmp_path / "s", remotes)
    draw(tmp_path / "s", remotes, overwrite=True)
    draw(tmp_path / "s", remotes, label="java-v2")               # labels sit side by side
    assert sorted(os.listdir(tmp_path / "s")) == [
        "open-source-repository-sample.csv", "open-source-repository-sample.derivation.json",
        "open-source-repository-sample.java-v2.csv",
        "open-source-repository-sample.java-v2.derivation.json"]


def test_an_interrupted_run_resumes_to_the_same_sample(tmp_path, remotes):
    draw(tmp_path / "whole", remotes)
    calls = []

    def flaky(url, dest, history, hint=None):
        if len(calls) == 1:
            raise KeyboardInterrupt
        calls.append(url)
        return measure.measure(url, dest, history, hint=hint)

    with pytest.raises(KeyboardInterrupt):
        draw(tmp_path / "r", remotes, measure_fn=flaky)
    assert os.path.exists(tmp_path / "r" / files.names()[2])
    with pytest.raises(RuntimeError, match="different parameters"):
        draw(tmp_path / "r", remotes, params={**PARAMS, "seed": 9})
    draw(tmp_path / "r", remotes)
    key = lambda rows: [(r["full_name"], r["commit"], r["status"]) for r in rows]
    assert key(rows_of(tmp_path / "r")) == key(rows_of(tmp_path / "whole"))


# --- using a sample ---------------------------------------------------------------------------

def _advance(remote_url):
    """A commit made on the remote AFTER sampling."""
    path = remote_url[len("file://"):]
    with open(os.path.join(path, "src/New.java"), "w") as fh:
        fh.write(_java(2))
    _git(path, "add", "-A")
    _git(path, "commit", "--quiet", "-m", "later")


def _head(path, *rev):
    return subprocess.run(["git", "rev-list", *(rev or ["HEAD"])], cwd=path,
                          capture_output=True, text=True).stdout.split()


@pytest.mark.parametrize("deep", [False, True])
def test_a_clone_holds_nothing_after_the_pinned_commit(tmp_path, remotes, deep):
    draw(tmp_path / "s", remotes, params=HISTORY)
    pinned = {r["full_name"]: r for r in files.read_sample(str(tmp_path / "s"))}
    for n in pinned:
        _advance(remotes[n])
    res = checkout.checkout(str(tmp_path / "s"), str(tmp_path / "repos"), deep=deep,
                            log=lambda *_: None)
    assert [r["error"] for r in res] == [None, None]
    for r in res:
        row = pinned[r["full_name"]]
        assert _head(r["path"])[0] == row["commit"]
        every = _head(r["path"], "--all")
        assert len(every) == (row["n_commits"] if deep else 1)   # all history, or one commit
        assert not os.path.exists(os.path.join(r["path"], "src", "New.java"))
        remotes_cfg = subprocess.run(["git", "remote"], cwd=r["path"], capture_output=True,
                                     text=True).stdout
        assert remotes_cfg == ""                                 # nothing to fetch later from


def test_an_incomplete_deep_clone_is_refused(tmp_path, remotes):
    draw(tmp_path / "s", remotes, params=HISTORY)
    row = files.read_sample(str(tmp_path / "s"))[0]
    err = checkout.clone_at(row["url"], row["commit"], str(tmp_path / "c"), deep=True,
                            expected_commits=row["n_commits"] + 1)
    assert err and "incomplete" in err
    assert checkout.clone_at(row["url"], row["commit"], str(tmp_path / "c"), deep=True,
                             expected_commits=row["n_commits"]) is None


def _mixed_remote(tmp_path):
    """A repository of source, build files and assets, served with partial-clone support (as
    GitHub serves it), so a filtered clone really fetches file contents lazily."""
    url = make_repo(tmp_path, "mixed", {
        "src/a/A.java": _java(2), "src/a/B.JAVA": _java(1), "pom.xml": "<project/>",
        "sub/pom.xml": "<project/>", "Dockerfile": "FROM x", "README.md": "hi",
        "img/logo.png": "not really a png", "lib/dep.jar": "not really a jar"}, commits=2)
    path = url[len("file://"):]
    _git(path, "config", "uploadpack.allowFilter", "true")
    _git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    return url, path, _head(path)[0]


def _tree(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            full = os.path.join(dirpath, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = fh.read()
    return out


@pytest.mark.parametrize("deep", [False, True])
def test_only_downloads_and_checks_out_just_the_matching_files(tmp_path, deep):
    url, remote, commit = _mixed_remote(tmp_path)
    dest = str(tmp_path / "c")
    assert checkout.clone_at(url, commit, dest, deep=deep, only=[".java", "pom.xml"]) is None
    full = _tree(remote)
    got = _tree(dest)
    assert sorted(got) == ["pom.xml", "src/a/A.java", "src/a/B.JAVA", "sub/pom.xml"]
    assert got == {p: full[p] for p in got}                      # byte-identical to the commit
    assert _head(dest)[0] == commit
    assert len(_head(dest, "--all")) == (2 if deep else 1)       # history as without `only`
    png = subprocess.run(["git", "rev-parse", f"{commit}:img/logo.png"], cwd=remote,
                         capture_output=True, text=True).stdout.strip()
    missing = subprocess.run(["git", "cat-file", "-e", png], cwd=dest, capture_output=True)
    assert missing.returncode != 0                               # never downloaded
    assert subprocess.run(["git", "remote"], cwd=dest, capture_output=True,
                          text=True).stdout == ""                # nothing to fetch later from
    assert subprocess.run(["git", "status", "--porcelain"], cwd=dest, capture_output=True,
                          text=True).stdout == ""


@pytest.mark.parametrize("bad", ["*.java", "src/A.java", "!x", "", "[jJ]ava", ".j?va"])
def test_only_refuses_a_glob_or_path_rather_than_match_nothing(bad):
    with pytest.raises(ValueError):
        checkout.sparse_patterns([".java", bad])


def test_a_kept_clone_is_remade_when_only_changes(tmp_path, remotes):
    draw(tmp_path / "s", remotes)
    repos = str(tmp_path / "repos")
    res = checkout.checkout(str(tmp_path / "s"), repos, only=[".kt"], log=lambda *_: None)
    assert [sorted(_tree(r["path"])) for r in res] == [[], []]
    res = checkout.checkout(str(tmp_path / "s"), repos, log=lambda *_: None)
    assert [len(_tree(r["path"])) for r in res] == [4, 4]       # a full clone, not reused


def test_for_each_hands_over_only_and_clones_just_those_files(tmp_path, remotes):
    draw(tmp_path / "s", remotes)
    seen = {}
    checkout.for_each(str(tmp_path / "s"),
                      lambda r: seen.update({r.full_name: (r.only, sorted(_tree(r.path)))}),
                      only=[".java"], log=lambda *_: None)
    assert seen == {n: ([".java"], [f"src/C{i}.java" for i in range(4)])
                    for n in ("o/big1", "o/big2")}


def test_run_and_checkout_refuse_a_bad_only(tmp_path, remotes):
    from open_source_repository_sampler import cli
    draw(tmp_path / "s", remotes)
    assert cli.main(["checkout", str(tmp_path / "s"), "--dest", str(tmp_path / "r"),
                     "--only", "*.java"]) == 2
    assert not os.path.exists(tmp_path / "r")


def test_for_each_runs_each_usable_repository_then_deletes_its_clone(tmp_path, remotes):
    draw(tmp_path / "s", remotes, params=HISTORY)
    seen = []

    def experiment(repo):
        seen.append((repo.full_name, repo.commit, repo.deep, len(_head(repo.path, "--all"))))
        assert os.path.isfile(os.path.join(repo.path, "src", "C0.java"))
        assert repo.row["status"] == "usable"
        return repo.full_name.upper()

    work = tmp_path / "work"
    out = checkout.for_each(str(tmp_path / "s"), experiment, deep=True, work_dir=str(work),
                            log=lambda *_: None)
    pinned = {r["full_name"]: r for r in files.read_sample(str(tmp_path / "s"))}
    assert sorted(seen) == sorted((n, r["commit"], True, r["n_commits"])
                                  for n, r in pinned.items())
    assert [o["result"] for o in out] == [o["full_name"].upper() for o in out]
    assert os.listdir(work) == []                                # each clone deleted after use


def test_for_each_records_failures_and_resumes_from_its_run_log(tmp_path, remotes):
    draw(tmp_path / "s", remotes)
    log_path = str(tmp_path / "run-log.jsonl")
    first = []

    def flaky(repo):
        first.append(repo.full_name)
        if len(first) == 2:
            raise ValueError("boom")

    out = checkout.for_each(str(tmp_path / "s"), flaky, run_log=log_path, log=lambda *_: None)
    assert [o["status"] for o in out] == ["done", "failed"]
    assert "ValueError: boom" in out[1]["error"]
    again = []
    out = checkout.for_each(str(tmp_path / "s"), lambda r: again.append(r.full_name),
                            run_log=log_path, log=lambda *_: None)
    assert [o["status"] for o in out] == ["skipped", "done"]     # only the failure is redone
    assert again == first[1:]


def test_a_deep_run_on_a_shallow_drawn_sample_gets_the_whole_history(tmp_path, remotes):
    draw(tmp_path / "s", remotes)                                # no n_commits recorded
    counts = {}
    checkout.for_each(str(tmp_path / "s"),
                      lambda r: counts.update({r.full_name: len(_head(r.path, "--all"))}),
                      deep=True, log=lambda *_: None)
    assert counts == {"o/big1": 3, "o/big2": 2}


def test_run_command_gets_each_repository_by_placeholder_and_environment(tmp_path, remotes):
    from open_source_repository_sampler import cli
    draw(tmp_path / "s", remotes)
    out = tmp_path / "out"
    out.mkdir()
    script = ('import os,sys; open(os.path.join(sys.argv[1], sys.argv[2].replace("/","_")),"w")'
              '.write(os.environ["SAMPLED_REPOSITORY_COMMIT"] + " " + sys.argv[3] + " "'
              ' + str(os.path.isdir(os.environ["SAMPLED_REPOSITORY_PATH"])))')
    code = cli.main(["run", str(tmp_path / "s"), "--deep", "--", sys.executable, "-c", script,
                     str(out), "{full_name}", "{deep}"])
    assert code == 0
    pinned = {r["full_name"].replace("/", "_"): r["commit"]
              for r in files.read_sample(str(tmp_path / "s"))}
    assert {f: (out / f).read_text() for f in os.listdir(out)} == {
        f: f"{c} true True" for f, c in pinned.items()}
    failing = cli.main(["run", str(tmp_path / "s"), "--", sys.executable, "-c", "exit(3)"])
    assert failing == 1


def test_verify_detects_an_edited_csv(tmp_path, remotes):
    draw(tmp_path / "s", remotes)
    assert checkout.verify(str(tmp_path / "s"), fetch=True, log=lambda *_: None) == []
    path = tmp_path / "s" / files.names()[0]
    path.write_text(path.read_text().replace("usable", "excluded", 1))
    problems = checkout.verify(str(tmp_path / "s"), log=lambda *_: None)
    assert problems and "changed after sampling" in problems[0]
