#!/usr/bin/env bash
# Step 3 — run the experiment on every repository of a sample.
#
#   ./step3_run.sh [--sample FILE] [--out DIR]
#       --sample FILE   default sample/open-source-repository-sample.csv
#       --out DIR       default results/<today>
#
# Template: the sample may come from step 2 or from anywhere else (another study's sample, to
# study exactly the same code). It is verified first. Then the sampler clones each usable
# repository at its pinned commit (add --deep for every commit up to it), runs the experiment
# on it, and deletes the clone. Re-running with the same --out resumes from the run log.
set -euo pipefail
cd "$(dirname "$0")"

SAMPLE="sample/open-source-repository-sample.csv"
OUT="results/$(date +%F)"
while [ $# -gt 0 ]; do
  case "$1" in
    --sample) SAMPLE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    *) echo "ERROR: unknown argument: $1"; exit 2 ;;
  esac
done
[ -f "$SAMPLE" ] || { echo "ERROR: no sample at $SAMPLE — run ./step2_sample.sh or pass --sample"; exit 1; }
mkdir -p "$OUT"

.venv/bin/open-source-repository-sampler verify "$SAMPLE"
# Replace the command after `--` with the experiment. {path} is the clone; {full_name},
# {commit}, {url}, {language} and {deep} are also available, and in the environment.
.venv/bin/open-source-repository-sampler run "$SAMPLE" --deep --run-log "$OUT/run-log.jsonl" \
    -- .venv/bin/my-experiment --repo {path} --out "$OUT/{full_name}"
