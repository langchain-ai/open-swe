# Remote mode

Remote mode lets `local_fix_agent.py` reason on one machine while operating on a repo on another machine over SSH.

## Quick example

```bash
python local_fix_agent.py --target edge-01 --repo /srv/app --test-cmd "pytest tests/test_x.py -q"
```

Wrapper-style:

```bash
fixit --target edge-01 --repo /srv/app "pytest -q"
```

## Architecture

The split is simple:

- local machine: reasoning, planning, scoring, pattern memory, metrics, run artifacts
- remote machine: command execution, git operations, file reads/writes, test execution, structural validation

ASCII view:

```text
+-------------------------+           SSH / SCP            +----------------------+
| local GPU / operator box| -----------------------------------------------> | remote repo host     |
|                         |                                                 |                      |
| - prompt assembly       | <----------------------------------------------- | - git commands       |
| - hypothesis / planning |               stdout / stderr / file content     | - test commands      |
| - scoring / rollback    |                                                 | - file reads/writes  |
| - metrics / memory      |                                                 | - repo validation    |
| - run artifacts         |                                                 | - structural checks  |
+-------------------------+                                                 +----------------------+
```

This is a thin transport layer. The repair logic does not fork into a separate remote algorithm.

## What stays local

- model calls
- hypothesis generation
- planning
- scoring
- strategy selection
- pattern memory
- recent-run memory
- run metrics
- run artifacts

## What runs remotely

- shell commands
- test commands
- git status/diff/restore/commit helpers
- repo existence checks
- file reads
- file writes, replace, and append operations
- structural validation triggered through remote commands

## SSH multiplexing

Remote mode opens one SSH master connection per run and reuses it for:

- remote commands
- `scp` file transfer

Current implementation details:

- unique `ControlPath` per run
- host-tagged socket path with PID and short random suffix
- `ControlPersist=3600`
- `ConnectTimeout=10`
- `ServerAliveInterval=15`
- `ServerAliveCountMax=3`
- explicit shutdown of the master session at the end of the run
- one reopen attempt if the master session drops unexpectedly

What this does for the operator:

- lower latency across many short remote commands
- less SSH handshake overhead
- fewer collisions between concurrent runs

## What remains local in remote mode

Artifacts and memory stay local. In current behavior, remote runs do not write the following into the remote repo:

- `.fix_agent_memory.json`
- `.fix_agent_metrics.json`
- `.fix_agent_recent.json`
- `.fix_agent_runs/`

This is inferred directly from the current `state_storage_path()` logic.

## Safety model

Remote mode is conservative by default:

- if `--mode` is not explicitly set, remote runs bias toward `safe`
- the repo path is checked before the repair loop begins
- path validation refuses repo escapes
- blocked remote conditions stop the run early
- commit safety, rollback, validation, and scoring still run through the same shared logic as local mode

## Typical remote failures

### Connectivity

Examples:

- host not found
- no route to host
- connection refused
- network unreachable
- Tailscale or similar reachability failure

What you will see:

```text
BLOCKED: remote connectivity issue
```

### Auth

Examples:

- `Permission denied (publickey)`
- SSH authentication failure

What you will see:

```text
BLOCKED: remote SSH auth issue
```

### Repo path missing

The host is reachable, but the remote repo path is wrong or absent.

What you will see:

```text
BLOCKED: remote repo path not found
```

### Repo path permission issue

The remote login works, but the repo directory is not accessible to the SSH user.

What you will see:

```text
BLOCKED: remote repo path permission issue
```

### File write permission issue

The repo is reachable, but write operations fail during `scp` or the remote move.

What you will see:

```text
BLOCKED: remote file write permission issue
```

### Timeout

Remote session setup, repo checks, commands, and `scp` are bounded by timeouts.

What you will see:

```text
BLOCKED: remote command timed out
```

### Session dropped

If the master session dies mid-run, the tool reopens it once. If that fails:

```text
BLOCKED: remote session dropped
```

## Operator guidance

- Start with a narrow test command. Remote round trips are still slower than local execution.
- Use `--dry-run` first on sensitive branches or unfamiliar remote repos.
- Verify raw SSH access before troubleshooting the repair logic.
- If startup blocks immediately, fix the remote environment before retrying the agent.
- Review local artifacts after a remote run; they are the source of truth for the run summary and metrics.
