# Runbook

This is the main operator doc for `local_fix_agent.py`.

## What Do I Do?

Most runs look like this:

```bash
fixit pytest tests/test_x.py -q
./scripts/fixpublish.sh
```

Use the first command to make and validate a focused fix. Use the second command to run the required finalizer.

Normal flow:

```text
fix or edit
-> validate
-> finalize
-> update docs if needed
-> publish
-> verify PR mergeability
```

If you remember only one rule, remember this:

- validation success is not completion
- the run is complete only after `./scripts/fixpublish.sh`

## What Is Happening?

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

## Common Tasks

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

This is the normal final step after successful changes.

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

## Why Did It Do That?

The workflow is split on purpose:

- validation proves a specific repo state
- before validation or repair, the agent runs a pre-task git check when the repo is a git checkout
- that check requires a clean working tree and stops immediately if there are uncommitted changes
- `origin` is treated as the source of truth for your forked branch, so the agent fetches `origin` and merges `origin/<current-branch>` first
- if an `upstream` remote exists, the agent fetches it, detects its default branch automatically, and merges `upstream/<default-branch>` into the current branch second
- the sync strategy is merge-first with normal git fast-forward behavior when available; it never hard-resets or force-pushes
- any sync conflict is a blocking handoff and the agent prints the conflicting files
- finalization decides whether that validated state should publish, noop, or block
- docs updates happen inside finalization so published code and docs stay together
- branch alignment happens before publish so the PR is more likely to be mergeable immediately
- PR mergeability is checked again after publish as a final safety net

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
- Safe publishable files may be auto-staged by default, but publish still blocks unless the re-audit shows the intended paths are actually staged.
- `--no-auto-stage` disables that convenience path and forces an exact manual staging handoff instead.
- `--explain-staging` prints the per-file classification, publishability decision, and staging action for auditability.
- Use live probing only when a script depends on API, auth/proxy, or HLS/M3U8 behavior; it is a bounded debugging tool, not a default step.
- The branch is aligned with its base branch before publish when that can be done safely.
- PR mergeability is checked after publish as a final safety net.
- Ambiguous merge conflicts block instead of being guessed through.

## Blocked States

Blocked means the tool found a point where automatic continuation would be unsafe, misleading, or too ambiguous.

Examples:

- no reproducible validation command
- merge conflict that cannot be safely auto-resolved
- publish blocked by validation
- docs refresh changed the repo state and revalidation failed
- branch alignment introduced conflicts that could not be resolved safely

Blocked is not a crash. It is an intentional stop with evidence and next steps.

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

## Operator Checklist

After a successful run:

- review the diff
- confirm the validation target still passes
- run a broader suite if the change deserves it
- run the finalizer if it has not already run
- read the publish result and PR mergeability result separately

## How Is It Implemented?

The operator mental model above is the part you should carry around day to day. The details below matter when you need to explain a surprising result or debug the workflow.

## Advanced Notes

- `--no-finalize` intentionally stops before the required finalizer and is reported as incomplete
- `--publish-only` uses the current repo state but still respects validation gating
- the finalizer can create a validation record itself when one is missing
- post-publish PR mergeability verification remains active even when pre-publish base alignment succeeds

<!-- fix-agent-prepublish-runbook:start -->
## Pre-Publish Docs Check

Real publish now includes a docs gate after validation succeeds and before branch/commit/push work starts.
The agent detects documentation impact, refreshes affected docs in the same change set, reruns validation, and blocks publish if docs repair or revalidation fails.
Default docs refresh mode when triggered: `rewrite`.
<!-- fix-agent-prepublish-runbook:end -->
