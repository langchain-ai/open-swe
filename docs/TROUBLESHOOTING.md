# Troubleshooting

This page is for operator-visible failures and blocked states.

## Blocked-state summary

| Blocked type | Meaning | Suggested next action |
|---|---|---|
| `remote connectivity issue` | SSH target is not reachable, resolvable, or accepting connections | Verify `ssh <target>` manually and fix network/VPN/Tailscale reachability |
| `remote SSH auth issue` | SSH authentication failed | Verify SSH key, agent, user, and host access |
| `remote repo path not found` | The host is reachable but `--repo` does not exist remotely | Confirm the repo path on the remote host and rerun |
| `remote repo path permission issue` | SSH login works but the repo directory is not accessible | Fix directory ownership or permissions on the remote host |
| `remote file write permission issue` | The repo is reachable but file writes fail | Fix file or directory write permissions for the SSH user |
| `remote command timed out` | Remote session setup, repo check, command, or `scp` exceeded the timeout | Check host responsiveness, narrow the command, and retry |
| `remote session dropped` | The persistent SSH master session died and reopen failed | Verify SSH stability and rerun once the remote host is stable |
| `no reproducible failing test command` | The tool has no concrete command to reproduce the failure | Supply a narrow failing command or reuse recent state |
| `repeated candidate validation rejection` | Candidate patches keep failing pre-commit validation | Inspect the diff, narrow the target, or rerun with a more appropriate mode |
| `repeated stagnation without meaningful progress` | Attempts are not improving the failure materially | Narrow the target, inspect the diff, or escalate to `deep` |

## No reproducible failing test command

Symptom:

```text
BLOCKED: no reproducible failing test command
```

Meaning:

- no explicit `--test-cmd`
- no reusable recent failure
- no reusable last test command
- no config default

What to do:

```bash
python local_fix_agent.py --repo /path/to/repo "pytest tests/test_x.py -q"
```

Use the smallest command that reproduces the failure reliably.

## Repo path missing

### Local repo path missing

Meaning:

- the local path passed to `--repo` does not exist

What to do:

- verify `--repo`
- run from inside the intended git repo if you want auto-detection

### Remote repo path missing

Symptom:

```text
BLOCKED: remote repo path not found
```

Meaning:

- the remote host is reachable
- the repo path does not exist remotely, or is not accessible as a directory

What to do:

```bash
ssh edge-01 'test -d /srv/app && echo ok'
```

Then rerun with the correct `--repo`.

## SSH and connectivity issues

### Remote connectivity issue

Common evidence:

- host not found
- connection refused
- no route to host
- timeout during connect
- Tailscale-style reachability failure

What to do:

```bash
ssh edge-01
```

If this does not work reliably, fix connectivity first.

### Remote SSH auth issue

Common evidence:

- `Permission denied (publickey)`
- authentication failure

What to do:

- verify the SSH user
- verify keys or agent forwarding
- verify the target host accepts the expected key

## Permission problems

### Remote repo path permission issue

Meaning:

- SSH login works
- the repo directory is not accessible to the SSH user

What to do:

- check repo ownership
- check directory permissions
- verify the SSH user is the expected repo user

### Remote file write permission issue

Meaning:

- read access may work
- file updates fail during `scp` or remote move

What to do:

- verify write permissions on the repo path
- verify ownership of the target files

## Repeated candidate validation rejection

Symptom:

```text
BLOCKED: repeated candidate validation rejection
```

Meaning:

- candidate patches were generated
- pre-commit validation rejected them repeatedly

What to do:

- inspect `diff.patch`
- rerun with `--dry-run` if you are not already using it
- narrow the test target
- consider `--mode deep` only after narrowing the validation command

## Repeated stagnation

Symptom:

```text
BLOCKED: repeated stagnation without meaningful progress
```

Meaning:

- repeated attempts are not moving the failure meaningfully
- the agent is likely missing context or using the wrong scope

What to do:

- narrow the validation command
- inspect the last diff manually
- switch from `quick` or `safe` to `deep` if the scope truly requires it

## Remote command timed out

Symptom:

```text
BLOCKED: remote command timed out
```

Meaning:

- remote session setup, repo check, remote command execution, or `scp` exceeded the timeout

What to do:

- check remote host responsiveness
- run the same command manually over SSH
- narrow the target command
- retry when the remote host is less busy

## Remote session dropped

Symptom:

```text
BLOCKED: remote session dropped
```

Meaning:

- the persistent SSH session died mid-run
- the tool attempted one reopen
- the reopen failed

What to do:

- verify SSH stability outside the tool
- check VPN/Tailscale stability if used
- rerun after the remote host is stable

## External dependency or credential issues

Common evidence:

- API key errors
- unauthorized or forbidden responses
- service unavailable
- repeated rate-limit signals

What to do:

- fix the external dependency first
- rerun once the dependency is healthy and reachable

## Useful operator checks

Local:

```bash
git status --short
pytest tests/test_x.py::test_parse -q
```

Remote:

```bash
ssh edge-01
ssh edge-01 'cd /srv/app && pytest tests/test_x.py::test_parse -q'
```
