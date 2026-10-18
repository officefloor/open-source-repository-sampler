#!/usr/bin/env bash
# Step 2 — draw the sample this experiment runs on, into sample/.
#
#   ./step2_sample.sh [--seed N] [any other `open-source-repository-sampler sample` flags]
#
# Template: set this experiment's criteria in CRITERIA below, once, so they are part of the
# experiment's code. Extra flags are passed straight through. An interrupted run resumes when
# re-run with the same flags. Skip this step entirely when reusing an existing sample.
set -euo pipefail
cd "$(dirname "$0")"

CRITERIA=(--langs java --target 30 --min-functions 50 --min-commits 500)
OUT="sample"

.venv/bin/open-source-repository-sampler sample "${CRITERIA[@]}" --out-dir "$OUT" "$@"

echo
echo "Step 2 complete. Next, run the experiment on this sample:"
echo "    ./step3_run.sh --sample $OUT/open-source-repository-sample.csv"
