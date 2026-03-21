# Runbook

This is the main operator doc for `local_fix_agent.py`.

## Overview

The tool is built for a simple operating model:

```text
pick a narrow target
-> let the agent fix or edit
-> validate
-> finalize
-> update docs if needed
-> publish
-> verify PR mergeability
```

The important mental shift is that validation is not the last step.

Completion requires the canonical finalizer:

```bash
./scripts/fixpublish.sh
```

## Mental Model

Use this split when reasoning about the system:

- Codex:
  edits files, runs the requested validation, and should always run the finalizer after successful changes
- The agent:
  stores validation records, decides whether docs need updates, aligns the branch with its base branch when safe, publishes, and checks PR mergeability
- The operator:
  chooses the target command, reviews the result, and resolves only truly ambiguous blocked states

## Common Workflows

### 1. Fix a failing script or test

```bash
fixit pytest tests/test_x.py::test_parse -q
```

Use the smallest command that reproduces the problem. A narrow target gives the agent the best chance of making a safe, local fix.

### 2. Dry-run before changing anything

```bash
fixit --dry-run pytest tests/test_x.py -q
```

Use this when you want the agent to inspect and plan without committing a publishable result yet.

### 3. Finalize and publish

```bash
./scripts/fixpublish.sh
```

This is the normal final step after successful changes. The finalizer:

- ensures a commit-linked validation record exists
- checks meaningful changes
- detects docs drift
- updates docs if needed
- reruns validation if docs or code changed
- aligns the branch with its base branch when safe
- publishes
- verifies PR mergeability

### 4. Publish the current repo state directly

```bash
./scripts/publishcurrent.sh
```

Use this when you already know the current repo state is what you want to publish and you are intentionally skipping the repair loop.

### 5. Reuse recent context

```bash
python local_fix_agent.py --continue
python local_fix_agent.py --last
python local_fix_agent.py --from-last-failure
```

Use these when you want to pick up a recent validation target or recent run context instead of restating everything.

### 6. Import a script into training

```bash
python local_fix_agent.py --script /path/to/example.py --add-to-training
```

The script goes through candidate import, sanitization, validation, optional repair, and promotion into the private pattern repo only if it becomes a safe curated example.

### 7. Inspect learned patterns

```bash
python local_fix_agent.py --list-patterns
python local_fix_agent.py --list-patterns --filter-state curated_trusted
python local_fix_agent.py --list-pattern-sources
```

## Key Concepts

### Validation record

A validation record says that a specific commit was validated successfully. Publish is tied to that recorded state.

### Finalizer

The finalizer is [`./scripts/fixpublish.sh`](../scripts/fixpublish.sh). It is the required last step after successful edits.

### Meaningful changes

Meaningful changes are the files that matter for publish decisions:

- code
- tests
- docs
- scripts
- behavior-relevant config

Known machine-local state files are ignored.

### Pattern repo

The pattern repo is the local private training repo for learned script patterns. It stores sanitized, curated examples.

### Promotion states

- `candidate`
- `curated_experimental`
- `curated_trusted`

These are different from trust level. Promotion state describes where a source is in the curation process. Trust level describes how strongly it should influence normal runs.

## Safety Rules

- Do not treat a passing validation command as completion.
- Always run the finalizer after successful changes.
- The finalizer is the only canonical publish path.
- Docs updates happen inside finalization, not as a separate ad hoc step.
- Publish/noop decisions are based on meaningful changes, not only on working-tree noise.
- The branch is aligned with its base branch before publish when that can be done safely.
- PR mergeability is checked after publish as a final safety net.
- Ambiguous merge conflicts block instead of being guessed through.

## Common Commands

Local repair:

```bash
fixit pytest tests/test_x.py -q
```

Remote repair:

```bash
fixit --target edge-01 --repo /srv/app "pytest tests/test_x.py -q"
```

Explain current context:

```bash
python local_fix_agent.py --last --explain-only
```

Canonical finalizer:

```bash
./scripts/fixpublish.sh
```

Direct publish-current path:

```bash
./scripts/publishcurrent.sh
```

List trusted patterns:

```bash
python local_fix_agent.py --list-patterns --filter-state curated_trusted
```

## How To Think About Blocked States

Blocked means the tool found a point where automatic continuation would be unsafe, misleading, or too ambiguous.

Examples:

- no reproducible validation command
- merge conflict that cannot be safely auto-resolved
- publish blocked by validation
- docs refresh changed the repo state and revalidation failed
- branch alignment introduced conflicts that could not be resolved safely

Blocked is not a crash. It is an intentional stop with evidence and next steps.

## Operator Checklist

After a successful run:

- review the diff
- confirm the validation target still passes
- run a broader suite if the change deserves it
- run the finalizer if it has not already run
- read the publish result and PR mergeability result separately

## Advanced Notes

- `--no-finalize` intentionally stops before the required finalizer and is reported as incomplete
- `--publish-only` uses the current repo state but still respects validation gating
- the finalizer can create a validation record itself when one is missing
- post-publish PR mergeability verification remains active even when pre-publish base alignment succeeds
