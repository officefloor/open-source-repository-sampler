# Releasing open-source-repository-sampler

Distribution is automated by `.github/workflows/publish.yml`. Creating a `vMAJOR.MINOR.PATCH`
tag on `main` and pushing it builds the sdist and wheel, and publishes them to PyPI by Trusted
Publishing (OIDC, no API token). No tags are created by anything else.

## Version scheme

Tags are `vMAJOR.MINOR.PATCH`, e.g. `v0.1.0`, made by hand on a commit of `main`. The tag is
the only place the version is set. setuptools-scm reads it at build time and stamps it, with the
commit, into `open_source_repository_sampler/_version.py`. That stamp is how a pip-installed
sampler records which commit of itself drew a sample. The workflow refuses a tag that is not
`vMAJOR.MINOR.PATCH`, a tag not on `main`, and a build whose version differs from the tag. A
published version can never be replaced on PyPI, so a fix is always a new tag.

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

## What a release produces

- the sdist and wheel on PyPI, with a signed provenance record (attestation) tying them to this
  repository, the tagged commit and the workflow that built them;
- a GitHub Release for the tag, with generated notes and the same files attached.

## Cutting a release

```bash
git switch main && git pull
git tag -a v0.1.0 -m "open-source-repository-sampler 0.1.0"
git push origin v0.1.0
```

If a release run failed before the publisher was set up, re-run its failed jobs (Actions >
the run > Re-run failed jobs, or `gh run rerun <run-id> --failed`). The re-run uses the
workflow and code as they were at that tag.
