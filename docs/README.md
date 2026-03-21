# Operator Guide

`local_fix_agent.py` is a repair-focused CLI. It takes a reproducible command, gathers targeted context, edits conservatively, reruns validation, and reports either a successful fix or an explicit blocked state.

## Core Modes

- Normal repair: run the agent against a failing command.
- `--publish`: publish the last validated repair result.
- `--publish-only`: publish the current repo state without running the repair loop.
- `--explain-only`: show resolved settings and artifact locations without running the loop.

## Quick Commands

```bash
fixit pytest tests/test_x.py -q
fixit --dry-run pytest tests/test_x.py -q
fixit --target edge-01 --repo /srv/app "pytest -q"
python local_fix_agent.py --last
python local_fix_agent.py --continue
python local_fix_agent.py --from-last-failure
python local_fix_agent.py --reuse-last-test --repo /path/to/repo
python local_fix_agent.py --last --explain-only
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr --publish-merge
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr --publish-merge --publish-merge-local-main
./scripts/fixpublish.sh
./scripts/publishcurrent.sh
```

## CLI Reference

### Context and execution

- `--repo`
- `--target`
- `--test-cmd`
- positional test command support
- `--mode`
- `--last`
- `--continue`
- `--from-last-failure`
- `--reuse-last-test`
- `--dry-run`
- `--explain-only`
- `--show-diff`

### Publish and docs

- `--publish`
- `--publish-only`
- `--publish-branch`
- `--publish-pr`
- `--publish-merge`
- `--publish-merge-local-main`
- `--publish-message`
- `--update-docs`

### Advanced/operator controls

- `--http-proxy`
- `--https-proxy`
- `--api-budget-run`
- `--api-budget-attempt`
- `--config`
- `--max-steps`
- `--max-file-chars`

## Run Modes

- `quick`: tighter, faster loop for narrow failures
- `safe`: default operator mode
- `deep`: broader diagnosis and escalation path
- `benchmark`: comparative/stress-style runs

## Wrappers

- `scripts/fixpublish.sh`
  - changes to repo root
  - exports `AI_PUBLISH_ALLOW_FORK=1`
  - runs `python local_fix_agent.py --last --publish --publish-pr`
- `scripts/publishcurrent.sh`
  - changes to repo root
  - exports `AI_PUBLISH_ALLOW_FORK=1`
  - runs `python local_fix_agent.py --publish-only --publish-pr`

## Publish Workflows

### Validated-run publish

Use `--publish` after a successful repair run. The tool publishes only the validated repair change set and blocks if unrelated working tree changes would be included.

### Publish-current workflow

Use `--publish-only` to stage and publish the current repo state. This mode is for operator-driven publishing when you already know what should be committed.

### PR and merge behavior

- `--publish-pr` creates or reuses a PR with GitHub CLI.
- `--publish-merge` creates or reuses the PR and then attempts a safe auto-merge.
- `--publish-merge-local-main` checks out `main` and pulls `origin main` after a successful auto-merge.
- Auto-merge is only allowed for self-owned fork PRs targeting `main`.
- Auto-merge uses squash merge and does not delete branches.

### Publish summary fields

- `resolved_target`
- `control_path`
- `state_loaded`
- `state_reset`
- `reused_fork`
- `transport_locked`
- `state_confidence`
- `pr_already_exists`
- `pr_merge_attempted`
- `pr_merge_success`
- `pr_merge_block_reason`
- `merged_pr_url`
- `local_main_synced`
- `final_status`
- `reason`
- `next_action`

## Blocked and Control-Path Semantics

Current control paths include:

- `blocked_missing_origin`
- `blocked_auth`
- `fork_push`
- `direct_origin_push`
- `noop`

Blocked summaries are explicit operator messages, not silent retries.

## Docs Drift Summary

The tool tracks documentation drift with:

- `docs_required`
- `docs_targets`
- `docs_reason`
- `docs_refresh_mode`

`docs_refresh_mode` is one of `none`, `patch`, or `rewrite`.
