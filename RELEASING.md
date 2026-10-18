# Releasing open-source-repository-sampler

Distribution is automated by `.github/workflows/publish.yml`: pushing a `v*` tag builds the
sdist and wheel and publishes them to PyPI by Trusted Publishing (OIDC, no API token).

## Version scheme

Tags are `vMAJOR.MINOR.PATCH`, e.g. `v0.1.0`. The version is not written anywhere in the code:
setuptools-scm reads it from the tag at build time and stamps it, with the commit, into
`open_source_repository_sampler/_version.py`. That stamp is how a pip-installed sampler records
which commit of itself drew a sample. A published version can never be replaced on PyPI, so
a fix is always a new tag.

## One-time setup: PyPI Trusted Publishing

1. Go to https://pypi.org/manage/account/publishing/ and add a **pending publisher** (the
   project does not exist on PyPI until its first upload):
   - PyPI project name: `open-source-repository-sampler`
   - Owner: `officefloor`
   - Repository name: `open-source-repository-sampler`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
2. The `pypi` environment already exists in this repository's Settings > Environments.
   Optionally, require a reviewer to approve each publish.

## Cutting a release

```bash
git tag -a v0.1.1 -m "open-source-repository-sampler 0.1.1"
git push origin v0.1.1
```

If a release run failed before the publisher was set up, re-run its failed jobs (Actions >
the run > Re-run failed jobs, or `gh run rerun <run-id> --failed`). The re-run uses the
workflow and code as they were at that tag.
