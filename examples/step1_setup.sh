#!/usr/bin/env bash
# Step 1 — set up the experiment, with a PINNED version of open-source-repository-sampler.
#
#   ./step1_setup.sh
#
# Template: copy into your experiment and add its own setup. The sampler version is pinned so
# every run of this experiment samples with the same code; change SAMPLER only deliberately.
# Before the sampler is on PyPI, pin it by git tag instead:
#   SAMPLER="open-source-repository-sampler @ git+https://github.com/officefloor/open-source-repository-sampler.git@v0.2.0"
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
SAMPLER="${SAMPLER:-open-source-repository-sampler==0.2.0}"

"$PYTHON" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet "$SAMPLER"
# .venv/bin/pip install --quiet -e .        # <- the experiment itself, if it is Python

.venv/bin/open-source-repository-sampler --version
cat <<'NEXT'

Step 1 complete. Next, either draw a sample (needs `gh auth login`):

    ./step2_sample.sh --seed 1

or skip step 2 and reuse an existing sample (e.g. another study's), then run:

    ./step3_run.sh --sample path/to/open-source-repository-sample.csv
NEXT
