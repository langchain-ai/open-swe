# `local_fix_agent.py`

Operator guide for [`local_fix_agent.py`](../local_fix_agent.py).

This tool runs a constrained repair loop against a reproducible failing command, usually a targeted `pytest` invocation. It reads context, makes small edits through a tool layer, reruns validation, and commits only after additional checks pass.

## Quick start

Local run:

```bash
python local_fix_agent.py --repo /path/to/repo --test-cmd "pytest tests/test_x.py -q"
```

Wrapper-style run:

```bash
fixit pytest tests/test_x.py -q
```

Dry-run:

```bash
fixit --dry-run pytest tests/test_x.py -q
```

Explain-only:

```bash
python local_fix_agent.py --last --explain-only
```

Remote run:

```bash
fixit --target edge-01 --repo /srv/app "pytest -q"
```

Recent-state reuse:

```bash
python local_fix_agent.py --last
python local_fix_agent.py --continue
python local_fix_agent.py --from-last-failure
```

## Overview

`local_fix_agent.py` is built for one job: take a failing command, usually a test, and work through a guarded fix loop until the failure is resolved or the tool can explain why it is blocked.

It is a good fit for:

- Python repos with runnable tests
- narrow, reproducible failures
- repeated local iteration
- remote execution against a repo reachable over SSH
- workflows where logs, metrics, diffs, and blocked reasons matter

It is not a good fit for:

- broad feature work with no concrete validation target
- systems where success cannot be checked by tests or a reliable command
- destructive workflows where you are not willing to inspect the diff first
- highly stateful external systems that are weakly represented by the available validation command

## Mental model

The system has four layers:

1. Resolve run context.
   It chooses the repo, test command, mode, recent-state reuse, proxy settings, and optional SSH target.
2. Gather context and reason locally.
   It parses the failure, ranks likely files, forms a hypothesis, builds a short plan, and decides how conservative the next edit should be.
3. Execute through tools.
   It reads files, searches the repo, edits through guarded write tools, runs tests, and uses git-aware operations for diffing and rollback.
4. Validate before commit.
   It scores the result, may compare multiple candidate patch states, runs structural and sandbox validation, and only then auto-commits unless `--dry-run` or the interactive prompt says otherwise.

In remote mode, reasoning still happens locally. Only command execution and file operations move to the remote host.

## Features

### Repair loop and strategy control

- Tool-based repair loop with explicit tools for reading, searching, editing, diffing, test execution, and git actions.
- Three repair modes:
  - `minimal_patch`
  - `test_first_diagnosis`
  - `broader_rewrite`
- Failure classification:
  - `syntax_error`
  - `import_error`
  - `assertion_failure`
  - `runtime_error`
  - `unknown`
- Attempt scoring based on:
  - whether the output improved
  - whether the failing test count moved
  - whether the failure type improved or regressed
  - whether the diff appears to have introduced new issues

### Reasoning gates before edits

- Diagnosis is required before edits in diagnosis mode.
- Diff-aware reasoning is required after failed attempts that already changed files.
- A short edit plan is required before writing.
- An edit scope is required before writing.
- Test expectation alignment is required when the failure parser can extract it.
- Relevant file reads are enforced before editing.

### Targeting and context acquisition

- Relevant-file ranking uses:
  - failing test output
  - traceback files
  - import/module names
  - test filenames
  - recently modified files
  - filename similarity
  - search hits
  - pattern-memory hints
- Controlled repository search:
  - limited searches per attempt
  - duplicate search blocking
  - search-trigger reasons logged to the operator
- Precision patch mode for high-confidence cases where one file or symbol is strongly implicated

### Validation, rollback, and commits

- Git-aware workflow with status, diff, restore, branch creation, and commit helpers.
- Best-attempt tracking and rollback pressure on regression.
- Structural safety checks before commit:
  - `python -m py_compile`
  - import checks for modified non-test modules
- Pre-commit sandbox validation.
- Multi-candidate patch evaluation with candidate scoring and best-candidate selection.
- Commit safety limits on changed-path count and diff size.

### Persistence and operator support

- Persistent pattern memory in `.fix_agent_memory.json`
- Rolling run metrics in `.fix_agent_metrics.json`
- Recent-run reuse in `.fix_agent_recent.json`
- Config defaults in `.fix_agent_config.json`
- Per-run artifacts in `.fix_agent_runs/`
- Blocked-state reporting with actionable operator messages
- Stress harness in [`stress_test_local_fix_agent.py`](../stress_test_local_fix_agent.py)

### Convenience features

- Preset run modes:
  - `quick`
  - `safe`
  - `deep`
  - `benchmark`
- `--dry-run`
- `--explain-only`
- `--show-diff`
- `--last`
- `--continue`
- `--from-last-failure`
- `--reuse-last-test`
- wrapper-friendly CLI with positional test command support

### Remote execution

- `--target <host>` to run against a remote repo over SSH
- persistent SSH multiplexing for commands and `scp`
- unique per-run control socket
- remote repo existence check before the repair loop starts
- remote blocked-state classification for:
  - connectivity
  - auth
  - repo/path problems
  - file-write problems
  - timeouts
  - dropped session

## CLI usage

### Basic local examples

```bash
python local_fix_agent.py --repo /path/to/repo --test-cmd "pytest tests/test_x.py -q"
python local_fix_agent.py --repo /path/to/repo "pytest tests/test_x.py::test_parse -q"
fixit pytest tests/test_x.py -q
```

### Dry-run examples

```bash
python local_fix_agent.py --repo /path/to/repo --dry-run --test-cmd "pytest tests/test_x.py -q"
fixit --dry-run pytest tests/test_x.py -q
```

### Explain-only examples

```bash
python local_fix_agent.py --repo /path/to/repo --test-cmd "pytest tests/test_x.py -q" --explain-only
python local_fix_agent.py --last --explain-only
```

### Recent-run reuse examples

```bash
python local_fix_agent.py --last
python local_fix_agent.py --continue
python local_fix_agent.py --from-last-failure
python local_fix_agent.py --reuse-last-test --repo /path/to/repo
```

### Remote target examples

```bash
python local_fix_agent.py --target edge-01 --repo /srv/app --test-cmd "pytest tests/test_x.py -q"
fixit --target edge-01 --repo /srv/app "pytest -q"
```

## Run artifacts

Each run gets a timestamped directory under `.fix_agent_runs/`.

Example:

```text
.fix_agent_runs/
└── 20260319-143522/
    ├── diff.patch
    ├── log.txt
    ├── metrics.json
    └── summary.json
```

Files:

- `summary.json`
  - summary text
  - recent-run comparison text
  - rerun/continue/full-suite commands
  - path to `diff.patch`
- `metrics.json`
  - metrics payload for that run
- `log.txt`
  - summary text
  - comparison text
  - rerun guidance
- `diff.patch`
  - filtered diff for the run

Sample `summary.json` shape:

```json
{
  "run_metrics": {
    "success": true,
    "total_attempts": 3,
    "blocked_reason": null
  },
  "summary": "=== RUN METRICS ===\nsuccess: True\n...",
  "comparison": "Run comparison: performance improved ...",
  "rerun_cmd": "python /path/to/local_fix_agent.py --repo \"/path/to/repo\" --mode safe --test-cmd \"pytest tests/test_x.py -q\"",
  "continue_cmd": "python /path/to/local_fix_agent.py --continue",
  "full_suite_cmd": "python /path/to/local_fix_agent.py --repo \"/path/to/repo\" --mode safe --test-cmd \"pytest -q\"",
  "diff_path": "/path/to/repo/.fix_agent_runs/20260319-143522/diff.patch"
}
```

The exact values vary by run. The current implementation writes a richer payload than the minimal example above.

Persistent files:

- `.fix_agent_memory.json`
- `.fix_agent_metrics.json`
- `.fix_agent_recent.json`
- `.fix_agent_config.json`

Remote note:

- In remote mode, these files are stored locally in the launcher environment rather than written into the remote repo. This is current implementation behavior.

## When not to use this

Avoid using the tool as-is when:

- you cannot provide a reproducible validation command
- you want unattended destructive changes without first using `--dry-run`
- the system depends heavily on external state that is not covered by tests or another runnable validation command
- rollback is weak or the working tree contains sensitive uncommitted work you have not reviewed

## Architecture summary

High-level flow:

1. Resolve inputs and defaults.
2. Build failure context and relevant file ranking.
3. Form a hypothesis and a short plan.
4. Enforce required reads and reasoning before edits.
5. Execute edits through tools.
6. Rerun validation and score the result.
7. Escalate, diversify, rollback, or continue.
8. Validate candidate patches and commit only if checks pass.

The transport layer is intentionally thin. The same repair logic is used for local and remote runs; remote mode swaps in SSH-based command and file execution.

## Detailed docs

- [Remote mode](./REMOTE_MODE.md)
- [Runbook](./RUNBOOK.md)
- [Troubleshooting](./TROUBLESHOOTING.md)
