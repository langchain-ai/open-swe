# Remote Mode

Remote mode lets the agent reason locally while operating on a repository over SSH.

## Basic Usage

```bash
python local_fix_agent.py --target edge-01 --repo /srv/app --test-cmd "pytest tests/test_x.py -q"
fixit --target edge-01 --repo /srv/app "pytest -q"
```

## What Happens Where

- Local machine:
  - model calls
  - planning and scoring
  - pattern memory
  - recent state
  - metrics
  - run artifacts
- Remote machine:
  - shell commands
  - git commands
  - file reads and writes
  - validation commands

## SSH Session Model

- one SSH master session per run
- one control socket per run
- reused for commands and file transfer
- one reopen attempt if the session drops unexpectedly

## Remote Blocked Behavior

Blocked remote conditions stop the run early and print a structured blocked summary. Typical categories include:

- remote connectivity issue
- remote SSH auth issue
- remote repo path not found
- remote repo path permission issue
- remote file write permission issue
- remote command timed out
- remote session dropped

## Operator Guidance

- verify raw `ssh <target>` before debugging the agent
- prefer a narrow target command
- use `--dry-run` first on sensitive remote repos
- inspect local artifacts after the run because memory and metrics remain local
