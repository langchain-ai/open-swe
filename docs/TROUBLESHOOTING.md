# Troubleshooting

This page covers operator-visible blocked states, publish blocks, and summary interpretation.

## Blocked States

### Remote blocked kinds

- `remote connectivity issue`
- `remote SSH auth issue`
- `remote repo path not found`
- `remote repo path permission issue`
- `remote file write permission issue`
- `remote command timed out`
- `remote session dropped`

### Publish control paths

- `blocked_missing_origin`
- `blocked_auth`
- `fork_push`
- `direct_origin_push`
- `noop`

## Common Publish Blocks

- origin missing or malformed
- GitHub CLI unavailable or unauthenticated
- unrelated working tree changes in validated-run publish
- branch setup failure
- staging failure
- push authentication failure
- missing fork target
- PR creation failure
- auto-merge blocked because the PR is not a self-owned fork merge
- auto-merge blocked by conflicts, review, or checks

## Auto-Merge Block Reasons

Typical messages include:

- authenticated GitHub user is unknown
- PR details could not be loaded from GitHub CLI
- PR base repo owner does not match authenticated user; this is not a self-owned fork merge
- PR head repo owner does not match authenticated user
- PR base branch is not `main`
- PR state is not `OPEN`
- PR is still a draft
- PR has merge conflicts
- PR mergeability is unknown
- required review changes were requested
- required review approval is still missing
- required checks pending
- required checks failing
- required reviews or checks are still blocking merge

## Docs Drift Signals

The tool also reports operator-doc drift:

- wrapper script added but not documented
- CLI flag added but not documented
- examples no longer match current commands
- publish flow changed but docs still reflect older behavior
- control-path names differ from docs

Summary fields:

- `docs_required`
- `docs_targets`
- `docs_reason`
- `docs_refresh_mode`
