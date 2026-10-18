# open-source-repository-sampler

Reproducible, seeded, stratified sampling of open-source GitHub repositories for empirical
software engineering and mining software repositories studies. Every sampled repository is
pinned to the commit that met the sampling criteria.

It exists so that experiments do their study and leave choosing the repositories to one
independent, auditable tool. Choosing the repositories separately from the analysis is the
honest defence against selection bias: no experiment can pick or drop a repository on its result.

## Two phases

1. **Sample** (`sample`): draw candidates and check each against the criteria. Each candidate is
   cloned at its pinned commit, inspected, given its CSV row, then deleted. The result is the CSV
   and its derivation.
2. **Run** (`run`, optional): read the CSV and, for each usable repository, clone it again at
   exactly that commit (shallow or deep), hand it to the experiment's hook, then delete it. The
   experiment only processes repositories; the sampler does all the plumbing.

Phase 2 can be run many times, by many experiments, against the same CSV.

## What it produces

Two files with fixed names, so they can go straight into a paper's ancillary data and be
recognised on sight:

| File | What it is |
|---|---|
| `open-source-repository-sample.csv` | One row per candidate drawn: where it is (`url`), the exact code (`commit`), its detected `language`, the criteria measures, `status` (`usable` / `excluded`) with the `reason`, and the `sampler_commit` that produced it |
| `open-source-repository-sample.derivation.json` | Opens with `about`: what the files are, and links to this repository, its documentation and source at the exact commit used, and the PyPI release, so a reader who finds the files in a paper's ancillary data can trace them here. Then how the sample was derived: the command as typed and fully resolved, every parameter, the sampler's version and commit, tool versions, the method in words, every GitHub query with its match count, the frozen candidate frame, and the CSV's sha256 |

Excluded candidates stay in the CSV, so it shows that nothing was hand-picked. Experiments use the
`usable` rows.

`--label java-v2` gives `open-source-repository-sample.java-v2.csv` (and its derivation), so
several samples can sit side by side.

## Install

From PyPI (a released version, pinned):

```bash
pip install open-source-repository-sampler==0.1.0
```

From git, for experiments not written in Python (the command runs on its own):

```bash
git clone --branch v0.1.0 https://github.com/officefloor/open-source-repository-sampler.git
python3 -m venv .venv && .venv/bin/pip install ./open-source-repository-sampler
```

It needs `git`, and the GitHub CLI `gh` authenticated (`gh auth login`) for drawing a sample.
Using an existing sample needs only `git`.

## Draw a sample

```bash
open-source-repository-sampler sample \
    --langs java --tiers high,mid,low --target 30 --seed 1 \
    --min-functions 50 --min-commits 500 \
    --exclude-owners my-org,my-user \
    --out-dir sample
```

- **Strata.** A stratum is a language × popularity tier: `high` (>5000 stars), `mid`
  (1000–5000), `low` (200–999). Each tier is split into star bands, because GitHub search returns
  at most 1000 results. Candidates are shuffled with the seed, and bands are interleaved in
  proportion to their size, so the draw spreads across the tier rather than taking its
  most-starred repositories.
- **Growth.** Each language keeps drawing until it has `--target` usable repositories, or its
  strata run out. That outcome is reported in the derivation, not hidden.
- **Pinned commit.** Each candidate is cloned at its default branch as it is when drawn. That
  commit is recorded, and every measure and criterion describes the code at that commit. The
  clone is deleted once its row is written.
- **Only as much clone as the criteria need.** The clone is shallow (depth 1) unless a history
  criterion (`--min-commits`, `--min-contributors`) is set. Only then is the full history
  fetched, blobless (every commit, but file contents only at the pinned commit), so the history
  counts are exact. Without a history criterion, `n_commits`, `n_contributors` and
  `first_commit_date` are left empty rather than guessed. Sampling starts by saying which
  clone it will use and why, before any download, so the cost is known up front. The
  derivation records it under `sampling_clone`: the mode (`shallow` or `full-history`), the
  exact `git clone` flags, and the criteria that required it.
- **Language detection.** The language is detected from the project's own source code (tests,
  vendored, generated and build directories excluded). A repository counts toward its detected
  language, so a C++ project labelled C on GitHub counts as C++.
- **Criteria.** The criteria are generic and fixed before any study looks at a repository:
  `--min-functions`, `--min-files-with-functions`, `--min-source-files`, `--min-nloc`,
  `--min-commits`, `--min-contributors`, `--min-language-share`.
- **Resume.** An interrupted run resumes from its checkpoint (from the same frozen frame) when
  you re-run the same command.
- **Safety.** A sample is never silently replaced; pass `--overwrite` to replace one. A sampler
  with uncommitted changes refuses to run, because its commit would not describe the code that
  ran. `--allow-dirty` runs anyway and records that it was dirty.

`open-source-repository-sampler sample --help` lists every option and its default.

## Use a sample

An experiment never has to clone anything itself. The sampler gets each usable repository
exactly as it was sampled, with **nothing after the pinned commit**:

- **shallow** (default): only the pinned commit's code.
- **deep** (`--deep`): every commit up to and including the pinned commit, and none after it.
  For history studies.

Both fetch by commit straight from the URL, with no tags and no remote configured, so later
commits are not in the clone and cannot be fetched into it by accident.

The history up to a pinned commit cannot change. A commit's id is a hash over its content and
its parents, so if a project later rewrites its history, the pinned commit is either still
served, with exactly the sampled history, or no longer served, and the clone fails loudly. It
never silently gives different code. A deep clone's commit count is also checked against the
`n_commits` recorded at sampling (when the sample was drawn with a history criterion), which
catches an incomplete clone.

### Run an experiment on every repository

`run` is the loop. For each usable repository it clones it (shallow or deep), runs your command
on it, then deletes the clone, so disk use stays at one repository at a time. Every clone can
be made again from the sample.

```bash
open-source-repository-sampler run sample/open-source-repository-sample.csv --deep \
    --run-log results/run-log.jsonl \
    -- my-experiment --repo {path} --out results/{full_name}
```

- **Placeholders.** `{path}`, `{url}`, `{commit}`, `{full_name}`, `{language}` and `{deep}` are
  replaced in the command. The same values are in the environment as `SAMPLED_REPOSITORY_PATH`,
  `SAMPLED_REPOSITORY_URL`, `SAMPLED_REPOSITORY_COMMIT`, `SAMPLED_REPOSITORY_FULL_NAME`,
  `SAMPLED_REPOSITORY_LANGUAGE` and `SAMPLED_REPOSITORY_DEEP`. This works for an experiment
  written in any language.
- **Failures.** A non-zero exit is recorded as a failure, and the loop moves on (`--fail-fast`
  stops at the first failure). It exits non-zero if any repository failed.
- **Resume.** With `--run-log`, every outcome is appended to that file, and a re-run skips
  repositories already done, so an interrupted run resumes and a failed repository is retried.
- **Options.** `--langs java` limits the run to a subset; `--work-dir` chooses where clones go;
  `--keep` keeps them.

From Python, the same loop:

```python
from open_source_repository_sampler import for_each

def experiment(repo):
    # repo.path, repo.commit, repo.full_name, repo.language, repo.deep, repo.row (the CSV row)
    ...

for_each("sample/open-source-repository-sample.csv", experiment, deep=True,
         run_log="results/run-log.jsonl")
```

### Other commands

```bash
open-source-repository-sampler checkout sample --dest repos [--deep]  # clone all, and keep them
open-source-repository-sampler verify sample --fetch  # CSV matches its derivation; commits fetchable
```

`read_sample(path)` returns the usable rows from Python. From any other language, the CSV is the
interface: filter on `status == usable`.

## In an experiment

The intended flow for a study, using numbered step scripts (templates in [`examples/`](examples)):

| Step | Script | What it does |
|---|---|---|
| 1 | `step1_setup.sh` | Installs the experiment and a pinned version of this sampler |
| 2 | `step2_sample.sh` | Draws the sample into `sample/` |
| 3 | `step3_run.sh` | Runs the experiment on every repository in `sample/open-source-repository-sample.csv` with `open-source-repository-sampler run` |

**Skip step 2** by supplying an existing sample: copy another study's two sample files into
`sample/`, or point step 3 at them. Experiments that run on the same files study exactly the
same code. A new seed gives a completely different set of repositories.

## Reproducibility

- **The same code.** The CSV pins every repository to a commit. `run` and `checkout` get exactly
  that code, and with `--deep` exactly that history, at any later date, as long as the host still
  serves it. If it no longer does (the project was deleted, or rewrote its history and the old
  commits were removed), the clone fails rather than returning different code. `verify --fetch`
  checks this for the whole sample.
- **The same method.** `reproduce.the_method` in the derivation is a ready-to-run script: it
  checks out this sampler at the commit that drew the sample and runs the fully resolved
  command. GitHub changes over time (stars move, repositories come and go), so re-running
  reproduces how the sample was drawn, not necessarily the identical list. The CSV is the record
  of what was drawn.
- **The same sampler.** Each row and the derivation carry the sampler's commit. For an installed
  (not cloned) sampler, the commit is stamped in at build time by setuptools-scm.
- **Unchanged files.** The derivation holds the CSV's sha256, so an edited CSV is detected by
  `verify`.

To cite a sample: *open-source-repository-sample.csv (sha256 …), drawn by
open-source-repository-sampler 0.1.0 @ `<commit>`, seed 1, on `<date>`*.

## Releasing

Push a `v*` tag. The `publish` workflow builds the release, stamping the tag's commit into the
package, and publishes it to PyPI by Trusted Publishing (set up once, as described in
`.github/workflows/publish.yml`).

## Tests

```bash
pip install -e '.[test]' && pytest
```

They run offline: GitHub search is stubbed, and local git repositories stand in for candidates.

## Licence

Apache-2.0.
