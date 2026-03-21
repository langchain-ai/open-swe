# Runbook

This runbook is for day-to-day use of `local_fix_agent.py`.

## Standard workflow

### 1. Narrow the failing command

Start with the smallest command that reproduces the problem.

Preferred:

```bash
fixit pytest tests/test_x.py::test_parse -q
```

Avoid starting with:

```bash
fixit pytest -q
```

Use the broader suite later, after the agent finds a candidate fix.

### 2. Choose a mode

Use:

- `quick`
  - one failing test
  - one obvious code path
- `safe`
  - default day-to-day mode
  - uncertain scope
- `deep`
  - repeated failure
  - stagnation
  - broader fix surface
- `benchmark`
  - comparative or stress-style runs

If you omit `--mode`, the tool infers one. Current behavior is approximate:

- narrow test target tends to choose `quick`
- recent failed runs in the same repo tend to choose `deep`
- broader or unknown scope tends to choose `safe`

### 3. Run the tool

Local:

```bash
fixit pytest tests/test_x.py -q
```

Dry-run:

```bash
fixit --dry-run pytest tests/test_x.py -q
```

Remote:

```bash
fixit --target edge-01 --repo /srv/app "pytest tests/test_x.py -q"
```

### 4. Read the result

At the end of a run, check:

- the success or blocked summary
- the confidence line after a successful fix
- the next-action suggestions
- the diff

If the run succeeded, inspect the patch before moving on.

### 5. Rerun validation

Do not stop at the narrow target. After a successful run:

1. inspect the diff
2. rerun the targeted test
3. run a broader suite if appropriate

Typical manual follow-up:

```bash
git diff
pytest tests/test_x.py -q
pytest -q
```

### 6. Escalate only when needed

Escalate to `deep` when:

- the same failure persists
- multiple attempts score flat or regress
- the fix probably spans multiple files or layers

Do not jump to `deep` first when a narrow test can localize the issue.

## Example walkthrough

Example problem:

- failing command: `pytest tests/test_x.py::test_parse -q`
- likely issue: one parser function returns the wrong value for one edge case

Run:

```bash
fixit pytest tests/test_x.py::test_parse -q
```

Typical behavior:

1. The tool resolves the repo and mode.
2. It reads the failing test and the most likely implementation file.
3. It forms a short hypothesis and plan.
4. It applies a small edit, reruns the target test, and scores the result.
5. If validation passes, it performs pre-commit checks and either commits or stops at `--dry-run`.

Typical outcome:

- target test passes
- the diff stays localized
- the summary points you to `diff.patch`, rerun commands, and the run metrics

Then do the usual operator follow-up:

```bash
git diff
pytest tests/test_x.py -q
pytest -q
```

## Recommended command patterns

### Fast local loop

```bash
fixit pytest tests/test_x.py::test_parse -q
```

### Safe review-first loop

```bash
fixit --dry-run --show-diff pytest tests/test_x.py -q
```

### Resume the last failed context

```bash
python local_fix_agent.py --from-last-failure
```

### Continue the same context

```bash
python local_fix_agent.py --continue
```

### Headless daily publish

Validated runs publish automatically after successful validation unless you opt out with `--no-publish-on-success`.

Publish now includes a pre-publish docs gate after validation succeeds and before commit/push/PR work starts. The tool detects whether operator docs need to change, updates the affected docs in the same change set when possible, reruns validation, and blocks publish if docs refresh or revalidation fails.

Publish ignores known local state/cache changes such as `.ai_publish_state.json` and `.fix_agent_docs_state.json` when deciding whether anything meaningful should be published. If only ignored files changed, the publish step reports `noop` and does not create a branch, push, or PR.

If the publish step noops because the current fingerprint already matches a previous successful publish, the output includes the prior publish branch, commit, and PR URL when one was recorded.

Run the agent and publish the validated result:

```bash
python local_fix_agent.py
python local_fix_agent.py --last
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish-pr
./scripts/fixpublish.sh
```

Disable automatic publish for a validated run:

```bash
python local_fix_agent.py --no-publish-on-success
```

Publish the current repo or branch state directly:

```bash
python local_fix_agent.py --publish-only
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr
./scripts/publishcurrent.sh
```

`fixpublish.sh`:

- changes into the repo root
- sets `AI_PUBLISH_ALLOW_FORK=1`
- runs a validated publish flow with PR creation enabled

`publishcurrent.sh`:

- changes into the repo root
- sets `AI_PUBLISH_ALLOW_FORK=1`
- publishes the current branch/repo state without requiring a recent failing test command
- if started from `main` in non-interactive mode and meaningful changes exist, auto-creates a safe publish branch before pushing
- if only ignored local state files changed, exits as `noop`
- prints docs-gate fields including `docs_checked_at_publish`, `docs_required`, `docs_updated`, `docs_refresh_mode`, and `docs_targets`

### Private training repo

The script-pattern training repo is separate from the working repo and defaults to:

```bash
~/.codex/memories/local_fix_agent_private_patterns
```

The tool creates that repo automatically on first use and preserves it across runs. Use `--reset-pattern-repo` only when you explicitly want to delete and rebuild the training repo.

Typical commands:

```bash
python local_fix_agent.py --import-pattern-files /path/to/example.py
python local_fix_agent.py --list-pattern-sources
python local_fix_agent.py --list-patterns
python local_fix_agent.py --relearn-patterns
python local_fix_agent.py --reset-pattern-repo
```

`--script /path/to/foo.py` does not import the script into training by default. To add it to training, use:

```bash
python local_fix_agent.py --script /path/to/foo.py --add-to-training
```

Imported scripts are sanitized before storage so obvious secrets and credential-bearing literals are replaced with placeholders while preserving the surrounding code structure and engineering patterns.

### Resolve settings only

```bash
python local_fix_agent.py --last --explain-only
```

## Review checklist

After a successful run:

- confirm the diff is localized
- confirm the changed files match the failure
- rerun the target command
- run a broader suite if the repo warrants it
- keep or discard the auto-commit based on your normal review standard

For sensitive changes, start with `--dry-run`.

## Remote workflow

Recommended order:

1. verify `ssh <target>` manually if the host is unfamiliar
2. run a narrow remote test target
3. prefer `--dry-run` first
4. inspect local run artifacts after the run

Example:

```bash
fixit --target edge-01 --repo /srv/app --dry-run "pytest tests/test_x.py::test_parse -q"
```

## When to stop and intervene

Stop the run and inspect manually when you see:

- repeated candidate validation rejection
- repeated stagnation
- low-confidence targeting
- remote auth, path, or connectivity failures
- external dependency failures not covered by tests

At that point, a tighter command or an environment fix is usually more useful than another blind retry.

<!-- fix-agent-prepublish-runbook:start -->
## Pre-Publish Docs Check

Real publish now includes a docs gate after validation succeeds and before branch/commit/push work starts.
The agent detects documentation impact, refreshes affected docs in the same change set, reruns validation, and blocks publish if docs repair or revalidation fails.
Default docs refresh mode when triggered: `patch`.
<!-- fix-agent-prepublish-runbook:end -->
