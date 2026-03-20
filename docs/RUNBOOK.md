# Runbook

This runbook is for day-to-day operation of `local_fix_agent.py`.

## Standard Repair Flow

1. Choose the smallest reproducible command.
2. Run locally first when possible.
3. Start with `safe` unless the scope is obviously tiny.
4. Review the summary, diff, and next actions.
5. Run broader validation yourself after a successful narrow fix.

## Common Commands

```bash
fixit pytest tests/test_x.py::test_parse -q
fixit --dry-run pytest tests/test_x.py -q
fixit --target edge-01 --repo /srv/app "pytest tests/test_x.py -q"
python local_fix_agent.py --continue
python local_fix_agent.py --from-last-failure
python local_fix_agent.py --last --explain-only
```

## Publish Commands

Validated-run publish:

```bash
python local_fix_agent.py --publish
python local_fix_agent.py --last --publish
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr --publish-merge
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr --publish-merge --publish-merge-local-main
./scripts/fixpublish.sh
```

Publish current repo state:

```bash
python local_fix_agent.py --publish-only
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr
./scripts/publishcurrent.sh
```

## Choosing Between Publish Modes

- Use validated-run publish when the agent just completed a successful repair and you want only that validated change set.
- Use publish-current when you want to stage and publish the repo state that already exists in the working tree.

## Safe Auto-Merge Rules

Auto-merge only proceeds when all of the following are true:

- authenticated GitHub user is known
- PR base repo owner matches the authenticated user
- PR head repo owner matches the authenticated user
- PR base branch is `main`
- PR state is `OPEN`
- PR is not draft and not conflicted
- required review is not blocking
- required checks are passing, or there are no required checks

If any condition fails, merge is skipped and the exact block reason is printed.

## When Docs Need Refresh

Operator docs are considered impacted when changes affect:

- CLI flags or help text
- run modes
- wrapper scripts in `scripts/`
- publish behavior or publish summaries
- blocked-state behavior or user-facing messages
- remote execution behavior
- operator workflow semantics reflected in the docs set

If `--update-docs` is set:

- `patch` updates only the relevant operator docs
- `rewrite` replaces the full operator docs set from a clean baseline

## Review Checklist

- inspect the diff
- confirm the target command still passes
- run a broader suite if the repo warrants it
- read blocked evidence carefully before retrying
- check docs summary fields when operator-visible behavior changed
