"""Reproducible, seeded, stratified samples of open-source repositories, each pinned to the
commit that met the sampling criteria.

For Python experiments:

    from open_source_repository_sampler import read_sample
    for repo in read_sample("sample/open-source-repository-sample.csv"):
        repo["url"], repo["commit"], repo["language"]

or let the sampler run the loop: each usable repository is cloned at its pinned commit (shallow,
or deep with every commit up to it and none after), handed to the experiment, then deleted:

    from open_source_repository_sampler import for_each
    def experiment(repo):            # repo.path, repo.commit, repo.full_name, repo.row, ...
        ...
    for_each("sample/open-source-repository-sample.csv", experiment, deep=True,
             run_log="results/run-log.jsonl")
"""
from .checkout import Repository, clone_at, for_each  # noqa: F401
from .files import read_sample  # noqa: F401
