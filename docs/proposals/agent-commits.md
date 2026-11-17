# Proposal: sampling repositories with declared agent commits (`--min-agent-commits`)

**Status:** built and released in 0.4.0 (18 November 2026). Asked for by `placement-gravity-study`,
which sets code an AI coding agent declares as its own beside code written before the
assistants existed (`--as-of`).

## The need

A study of agent-written code needs repositories where agents demonstrably committed. Choosing
them by hand, or filtering a sample inside the experiment, puts a sampling decision in the
experiment, which is what this tool exists to prevent. The decision belongs in the sample, with
its rule written down.

## The option

```bash
open-source-repository-sampler sample --langs java --tiers high,mid,low --target 30 --seed S \
    --min-functions 50 --min-files-with-functions 15 --min-agent-commits 30
```

`--min-agent-commits N` is a criterion like `--min-commits`: a repository is usable only when at
least N commits reachable from its pinned commit are **declared agent commits**. It is a history
criterion, so it makes the clone full-history (blobless).

A commit is a declared agent commit when one of the built-in signatures (`agents.py`) matches:

| Where | Matched against |
|---|---|
| `identity` | the author's and the committer's `Name <email>` |
| `trailer` | the `Name <email>` of each `Co-authored-by` line of the message |
| `message` | the whole message |

Version 1 of the signatures covers Claude Code, GitHub Copilot's coding agent, Cursor, Devin,
Codex, Jules, aider and OpenHands. Each carries its basis: `documented` (the agent's own
documentation or changelog) or `observed` (the identity its commits carry on GitHub).

The `observed` identities were checked against GitHub's commit search on 18 November 2026, each
returning commits under exactly that identity: `copilot-swe-agent[bot]
<198982749+Copilot@users.noreply.github.com>`, `Cursor Agent <cursoragent@cursor.com>`,
`devin-ai-integration[bot] <158243242+devin-ai-integration[bot]@users.noreply.github.com>`,
`google-labs-jules[bot] <161369871+google-labs-jules[bot]@users.noreply.github.com>` and
`openhands <openhands@all-hands.dev>`. The patterns match on the stable part (the account name or
the address), not on the numeric id. Not confirmed that way: `claude[bot]` as an author (the
search is not exact on names), which is kept because a GitHub App's account name is unique, so it
cannot match anyone else.

## What it records

- CSV: `n_agent_commits`, `first_agent_commit_date` (committer date of the earliest), `agents`
  (`name:count` joined by `;`). Measured whenever the history is read, by any history criterion.
- Derivation: `sampling_clone.agent_signatures`, the version and every signature used.

## What it does not show

- **It counts what commits say.** Assisted code committed under a person's name with no marker
  is invisible, and a marker can be added or removed by hand. The count is a lower bound.
- **The other commits are of unknown origin.** A repository's commits without a marker are not
  thereby written unaided.
- **Coverage is uneven across agents.** An agent whose default is to leave no marker (or whose
  users turn it off) is under-counted against one that always signs, so `agents` compares what
  is declared, not what is used.
- **Squash merges** keep one commit's metadata. A pull request of agent commits squashed by a
  person may keep only trailers, or nothing.
- **Not covered in version 1:** agents with no stable commit marker known to this release (for
  one, Gemini CLI), and markers that live outside the commit (pull-request authors, branch
  names, labels).

## Changing the signatures

The signatures are data fixed per release. A change (a new agent, a changed default trailer) is
a new `SIGNATURES_VERSION` in a new release, so two samples that counted differently say so in
their derivations. A study that needs its own list should count in its experiment, on a sample
drawn without this criterion, and say that its rule is the experiment's and not the sample's.

## Checks

`tests/test_agents.py`: each signature on a commit that carries it; what is not a declaration (a
person named Claude, a person as co-author, another bot, the address outside a trailer); the
counts over a history; the criterion excluding a repository below the bound; the derivation
recording the signatures; a shallow sample measuring none.
