# Terminal-Bench

Runs Robin on [Terminal-Bench 2.0](https://www.tbench.ai/) with [Harbor](https://harborframework.com/), the benchmark's own harness, on LangSmith sandboxes.

Harbor starts each task's environment, has the agent work in it, then runs the task's tests there.
`harbor_adapter.py` holds the environment and the agent Harbor runs.
`RobinSandbox` is Harbor's LangSmith environment with one change: it creates each task's sandbox with the `robin.external=true` label, which Robin requires before it attaches a sandbox it didn't create.
`RobinAgent` runs `robin run --sandbox <task sandbox>` beside Harbor with the task's instruction, so Robin's backend runs every command in the task's sandbox, where the tests grade it.
Robin itself is whatever that backend has deployed; nothing about it changes for the benchmark.

`harbor_runner.py` (`harbor-runner`) runs the job itself from a LangSmith sandbox, so nothing runs on your machine and it can close.
That sandbox is only the orchestrator: Harbor runs each task in a sandbox of its own, built from the task's image.

## Prerequisites

- [uv](https://docs.astral.sh/uv/).
- A LangSmith key that may create sandboxes, for the orchestrator and every task, from the **same LangSmith workspace as the Robin deployment**: Robin looks each task's sandbox up with its own key.
- A Robin backend (one with external sandboxes, `open-swe` #68103 or later) and a LangSmith **service key** (`lsv2_sk_…`) with the Workspace Admin role, from the LangSmith workspace that backend's deployment runs in.
  Robin runs a service key as the workspace's bot, so every task starts a system thread; it refuses keys from other workspaces and service keys without Workspace Admin.
  A personal access token (`lsv2_pt_…`) runs every task as you, on your model usage, so it is refused unless you pass `--personal-key`.

Put each in its own file under `secrets/` in this directory, named after the key (gitignored, and never uploaded):

```bash
mkdir -p -m 700 secrets
printf %s 'lsv2_...' > secrets/LANGSMITH_API_KEY_PROD    # creates the sandboxes
printf %s 'https://<robin backend>' > secrets/ROBIN_EVAL_BACKEND_URL
printf %s 'lsv2_sk_...' > secrets/ROBIN_EVAL_API_KEY
chmod 600 secrets/*
```

Or leave them in `open-swe/.env` under the same names, which `harbor-runner` reads last; only these keys are taken from it.
A variable exported in your shell wins over a file, and `secrets/` over `.env`.
They reach the orchestrator in a mode-600 file its run script sources, so anyone who can exec into sandboxes in that LangSmith workspace can read them while it exists.

Nothing of Robin's enters a task's sandbox: no CLI, no key and no proxy rule, and Robin leaves an attached sandbox's proxy and git config alone, so a task keeps the network policy Harbor gave it.
The CLI runs beside Harbor with only `PATH`, `HOME`, `OPEN_SWE_BACKEND_URL` and `OPEN_SWE_API_KEY`, set from the benchmark's own variable names so neither repoints your own `robin` or the desktop app at the eval backend; nothing goes through `--ak`, which Harbor writes into each job's `config.json`.

## Run

### From GitHub Actions

The "Robin: Terminal-Bench" workflow (`.github/workflows/robin_terminal_bench.yml`) runs Harbor on a GitHub runner with CI's staging service key, so it needs no local keys or `~/.harbor-runner/` records.
Its inputs are the deployment (staging or preview), model, effort, Harbor dataset, task globs, attempts, concurrency, and `harbor_args`, anything else for `harbor run`.
Each run writes a per-trial table to the job summary, is recorded as a LangSmith experiment on a dataset named after Harbor's (`terminal-bench-2.0`), and uploads the Harbor job as an artifact.

Start one and follow it from a terminal with `gh`; Ctrl-C only stops watching:

```bash
./run.sh gh anthropic/claude-opus-5-5 high                    # every task
./run.sh gh anthropic/claude-opus-5-5 high fix-git 'openssl-*' -- -l 5
ROBIN_TB_REF=my-branch ./run.sh gh anthropic/claude-opus-5-5  # the adapter on a pushed branch
```

The job times out after 6 hours, and runs only against staging or preview; for a longer run, prod, or a personal key, use `harbor-runner` below.

### Locally

`run.sh` wraps the commands below for any model Robin offers: `smoke` runs two tasks, follows them, pulls the results and deletes the sandbox; `full` starts every task detached.

```bash
./run.sh smoke google_genai/gemini-4-neon high
./run.sh full anthropic/claude-opus-5-5 medium
./run.sh status    # also pull, attach, down, ls
```

Add `--personal-key` to `smoke` or `full` to run with an `lsv2_pt_…` key.

Or directly, from this directory:

```bash
uv sync --locked
uv run --locked harbor-runner up anthropic/claude-opus-5-5 medium --detach
```

`up` takes the model and Robin's effort (both default to Opus 5.5 at medium), creates the orchestrator, uploads this directory without `.venv`, `jobs/`, `secrets/` or any `.env`, and starts `harbor run` there under `setsid nohup`, so it outlives every connection.
The orchestrator's idle stop is off: Harbor only calls out of it, which doesn't count as activity.
Arguments after `--` go to `harbor run`, so a few tasks first:

```bash
uv run --locked harbor-runner up --detach -- -d terminal-bench@2.0 -i fix-git -i openssl-selfsigned-cert
```

Then, from anywhere:

```bash
uv run --locked harbor-runner status   # running or exited, trials finished, passed
uv run --locked harbor-runner attach   # follow the log; Ctrl-C only detaches
uv run --locked harbor-runner pull     # copy jobs/<job>/ and its log into jobs/
uv run --locked harbor-runner down     # delete the orchestrator, after pulling
uv run --locked harbor-runner ls       # runs started from this machine
```

Each takes a run name and otherwise uses the newest; records live in `~/.harbor-runner/`.
`harbor-runner` names the agent with `--agent` because Harbor applies `-m` only to an agent given on the command line, and silently drops it for one defined in `job.yaml`.

Browse pulled results with `uv run --locked harbor view jobs`.
Each trial's `agent/robin.stdout` holds the Robin thread's URL, whose trace is in LangSmith, and `agent/robin.stderr` the CLI's progress and errors.

To also upload the job as a LangSmith experiment over a dataset of the tasks, pass Harbor's plugin:

```bash
uv run --locked harbor-runner up -- --plugin harbor_langsmith.plugin:LangSmithPlugin \
  --pk dataset_name=terminal-bench-2.0 --pk experiment_name=robin-<label>
```

## Adapter options

Set with `--ak key=value` after `--`:

| Option | Meaning |
|---|---|
| `effort` | Reasoning effort for the job's model (`-m`). Required with a model; `harbor-runner` sets it. |
| `version` | The robin release to run beside Harbor (`desktop-v<version>`). Defaults to the pinned one, whose digests the adapter carries. |
| `sha256` | The tarball's digest for another `version` on this machine; without it the adapter checks the release's own `robin-SHA256SUMS`. |
| `workdir` | The directory Robin works in (`--work-dir`); the image's `WORKDIR` when unset. |
| `personal_key` | Accept a personal access token; `harbor-runner --personal-key` sets it. |

## What to expect

- Robin's `execute` defaults to a 300-second timeout, so it runs a task's long builds in the background.
- When Harbor's agent timeout ends a trial, the adapter interrupts the CLI and waits up to 30 seconds for it to cancel the run and its workers on the backend, before the verifier starts.
  Processes Robin left running in the background stay, as they do when Robin finishes on its own.
- A trial counts as an attempt when Robin finishes or stops to ask a question; when the backend refuses the thread or the run fails, it is a Harbor error instead.
- Setup installs nothing in the task's image, so musl (Alpine) images and images whose package sources no longer resolve run as any other.
- Unsupported tasks (docker-compose, GPU, TPU, or Windows) fail per-trial during `start()` rather than aborting the whole Harbor job.
- Harbor records no trajectory for Robin; the verifier grades only the environment's final state.
