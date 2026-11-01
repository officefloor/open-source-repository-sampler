# Proposal: sampling repositories as they were on a date (`--as-of`)

**Status:** built (1 November 2026), as proposed; to be released. Written for
`long-method-slim-study`, which needs human-written code only: each repository as it stood
before AI coding assistants were in general use.

## The need

Today `sample` pins each candidate to its default branch **as it is when drawn**, and judges the
criteria on that commit. A study of code written before a date cannot use that:

- the pinned commit holds whatever was written since, by whatever means;
- the criteria (functions, files, lines) describe the code now, not then, so a repository that
  was tiny on the date can pass, and one that has since shrunk can fail;
- the frame includes repositories created after the date, which have no code from before it.

Re-pinning in the experiment (checking out an older commit in its hook) would leave the frame
and the criteria describing the present, and would put sampling decisions in the experiment,
which is what this tool exists to prevent.

## The option

```bash
open-source-repository-sampler sample --langs java --tiers high,mid,low --target 580 --seed S \
    --min-functions 50 --min-files-with-functions 15 --as-of 2021-06-01
```

`--as-of DATE` (an ISO date, the instant 00:00 UTC that day) changes three things, and nothing
else:

1. **The frame:** only repositories created before the date. The search's `created:` bands
   already split the frame by creation date (`frame.py`); the upper bound becomes the day
   before `--as-of`.
2. **The pinned commit:** for each candidate, the newest commit on its default branch's
   first-parent history whose **committer date** is before the instant. Found from the
   repository's commit history without trees or contents (`git fetch --filter=tree:0`, then
   `git rev-list --first-parent -1 --before=<instant> <default branch>`). Its tree is then
   fetched as today's pinned commit is. A candidate with no such commit is `excluded`, reason
   `no commit before as-of`.
3. **The criteria and the language** are judged on that commit, as they are today on the
   current one.

## What it records

- The derivation gains `parameters.as_of`, and the method in words says the frame and every
  commit are as of that date.
- The CSV's existing `commit` and `commit_date` are the as-of commit's. A new column,
  `head_commit`, keeps the default branch's commit when drawn, so a reader sees how far the
  repository has moved since.
- `verify` checks that each `commit` is the newest first-parent commit before the date, as well
  as everything it checks today.
- `run` needs no change: it clones the pinned commit, as now.

## What it does not fix, and the derivation should say

- **Survivorship.** Stars, the tiers and the frame come from GitHub today. A repository that
  existed on the date but was deleted or made private since is not in the frame, and a
  repository's tier is its popularity now, not then. GitHub's search has no history to correct
  either.
- **Default branch.** The branch is today's default; a repository that renamed its branch
  since (master to main) is followed through the rename by its history, but one that switched
  to an unrelated branch is read on the new one.
- **Committer dates can be wrong or rewritten.** Rebased or imported history can carry dates
  that are not when the code was written. First-parent history and committer (not author)
  dates keep this small; it is stated, not solved.

## Tests

- The frame query carries `created:..<day before as-of>`.
- On a fixture repository with commits either side of a date, the pinned commit is the newest
  first-parent commit before it, merges' second parents ignored.
- A repository created after the date never enters the frame; one with no commit before it is
  `excluded` with the reason above.
- Without `--as-of`, every output is byte-identical to today's.

## Cost

Finding the commit costs one treeless fetch of the history per candidate: commits only, small
even for large projects. The criteria clone is the same as today's.
