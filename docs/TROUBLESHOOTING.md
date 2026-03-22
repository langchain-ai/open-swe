# Troubleshooting

This page is for operator-visible failures, blocked states, and recovery steps.

## What Do I Do?

When a run ends in a way you do not expect, check these in order:

1. Did validation succeed?
2. Did the finalizer run?
3. Did publish succeed, noop, or block?
4. Is the PR mergeable?

The canonical finalizer is:

```bash
./scripts/fixpublish.sh
```

If a run ends after validation but before finalization, that is incomplete, not successful. The explicit incomplete outcome is reported as `finalization skipped (incomplete)`.

If you are using `python local_fix_agent.py --auto` or interactive quick mode, the agent will try to clean safe blockers, validate with the best available command, repair one clear validation failure, and continue automatically.
Those modes should only stop when they hit a real ambiguity, repeated validation failure, merge conflict, or missing required input.

## What Is Happening?

The system separates outcomes on purpose:

- validation proves a repo state
- finalization decides whether that state should publish
- publish says whether the branch was pushed or reused successfully
- PR mergeability says whether that published branch is actually ready to merge

## High-Level Meanings

### Validation succeeded

The current repo state passed the validation command and a validation record can be created or reused.

### Finalization succeeded

The canonical finalizer ran and completed its job:

- validation record confirmed
- docs checked and updated if needed
- branch aligned with base branch if needed
- publish attempted
- PR mergeability checked

### Noop

The finalizer decided there was nothing meaningful to publish. This usually means only ignored local state changed or the current publishable state already matches the last successful published state.

### Blocked

Blocked means the tool stopped intentionally because continuing would be unsafe or too ambiguous.

## Why Did It Do That?

Common reasons the workflow stops or surprises you:

- there is no successful validation record for the current commit
- docs refresh changed the repo and revalidation failed
- pre-publish base alignment hit a conflict or validation failure
- publish succeeded but PR mergeability still needed to be verified
- merge conflict resolution was too ambiguous to automate safely

## Common Publish Questions

### Why did publish not run?

Common reasons:

- no successful validation record for the current commit
- docs update changed the repo and revalidation failed
- pre-publish branch alignment blocked
- publish was explicitly skipped with `--no-finalize`

### Why did publish noop?

Most common explanations:

- only ignored local state files changed
- there were no meaningful changes since the last successful published state
- the current publishable state already matches a previous successful publish state

### Why did publish block on staging?

Most common explanations:

- `--no-auto-stage` was used, so the tool reported exact manual `git add -- <path>` commands instead of staging automatically
- a remaining publishable path was ambiguous or outside the safe auto-stage policy
- auto-stage ran, but the post-stage re-audit still found unstaged publishable files

When staging blocks, the publish summary reports:

- `auto_stage_attempted`
- `auto_stage_result`
- `auto_staged_paths`
- `remaining_unstaged_paths`
- `remaining_unstaged`
- `file_decisions`
- `staging_summary`
- `staging_decision_reason`
- `staging_reason`

The `next_action` field includes the exact manual staging commands the operator should run next.

Use `--explain-staging` when you need the full per-file classification. That output shows each file's `file_type`, whether it was publishable, whether it was auto-staged, ignored, or blocked, and the reason for that decision.

### Why did publish succeed but the workflow still not feel done?

Because publish success and PR mergeability are different checks. A branch can be pushed successfully while the PR still needs mergeability verification or repair.

## Blocked States

### No reproducible validation command

Meaning:

- the tool does not know what command proves the change is correct

What to do:

```bash
fixit pytest tests/test_x.py -q
```

Use the narrowest command that reproduces the real problem.

### Publish blocked by validation

Meaning:

- the current commit was not validated successfully
- or the validation record is stale and could not be refreshed successfully

What to do:

- rerun the agent with the correct validation target
- then run the finalizer again

### Docs refresh blocked publish

Meaning:

- the finalizer detected docs drift
- docs were updated or needed updates
- revalidation after that docs change did not succeed

What to do:

- inspect the docs-related diff
- rerun the validation command manually if needed
- fix the underlying validation issue
- rerun the finalizer

### Pre-publish base alignment blocked

Meaning:

- the finalizer tried to align the publish branch with its base branch before publish
- the merge was ambiguous or validation after alignment failed

What to do:

- read the alignment block reason
- inspect any listed conflicted files
- resolve the branch state
- rerun the finalizer

### Pre-task git check blocked

Meaning:

- the agent stopped before starting work because the repo was not safe to sync automatically
- common causes are a dirty working tree, a failed `git fetch`, or merge conflicts while integrating `origin/<current-branch>` or `upstream/<default-branch>`

What to do:

- if the output says the working tree is dirty, commit, stash, or remove the uncommitted changes first
- if the output lists conflicted files, resolve those files manually and rerun the same command
- if a fetch failed, verify that the `origin` and `upstream` remotes exist and are reachable
- review the printed `git_actions` list to see exactly which fetch and merge steps were attempted

### Manual merge required

When the agent cannot safely resolve a merge conflict, it prints:

```text
=== MANUAL MERGE REQUIRED ===
```

That section is the handoff. It tells you:

- which files are conflicted
- why auto-resolution was unsafe
- the minimal commands to resolve and continue

After resolving the merge manually, rerun the same agent command.

## Remote And SSH Problems

### Remote connectivity issue

Meaning:

- the SSH target is unreachable or unstable

What to do:

```bash
ssh edge-01
```

Fix connectivity first, then rerun the agent.

### Remote SSH auth issue

Meaning:

- the host is reachable but authentication failed

What to do:

- verify the SSH user
- verify the key or agent configuration
- confirm the host accepts that identity

### Remote repo path missing or unreadable

Meaning:

- SSH works
- the remote repo path does not exist or cannot be accessed

What to do:

```bash
ssh edge-01 'test -d /srv/app && echo ok'
```

## Pattern Repo Problems

### How do I see what the agent trusts?

```bash
python local_fix_agent.py --list-patterns
python local_fix_agent.py --list-patterns --filter-state curated_trusted
python local_fix_agent.py --list-pattern-sources
```

### What do the promotion states mean?

- `candidate`
  sanitized source exists, but it is not part of curated learning yet
- `curated_experimental`
  curated and usable, but weakly trusted
- `curated_trusted`
  curated and strongly trusted

### What if a pattern is being over-applied?

Use the manual controls:

```bash
python local_fix_agent.py --demote-pattern <pattern-id>
python local_fix_agent.py --forget-source <source-id-or-path>
```

## Useful Checks

Quick local checks:

```bash
git status --short
git diff
pytest tests/test_x.py -q
```

Pattern inspection:

```bash
python local_fix_agent.py --list-patterns --output json
```

Canonical finalizer:

```bash
./scripts/fixpublish.sh
```

## How Is It Implemented?

If you need the deeper model:

- validation records are commit-linked
- docs updates happen inside the finalizer
- pre-publish base alignment tries to make the branch mergeable before publish
- post-publish PR mergeability verification is the final safety net

## How To Think About This System When It Fails

- If validation failed:
  the fix itself is not proven yet
- If validation succeeded but finalization did not run:
  the work is incomplete
- If publish blocked:
  the finalizer found a safety problem
- If publish succeeded but PR mergeability failed:
  the branch exists, but the change is not yet safely ready to merge

That separation is intentional. It is what keeps the workflow understandable and safe.

<!-- fix-agent-prepublish-troubleshooting:start -->
## Publish Blocked By Docs Drift

If the pre-publish docs gate detects that operator docs need updates and automatic refresh or revalidation fails, publish is blocked.
The publish summary reports `docs_required`, `docs_updated`, `docs_refresh_mode`, and the affected `docs_targets` so the block reason is explicit.
<!-- fix-agent-prepublish-troubleshooting:end -->
