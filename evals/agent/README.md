# Agent Eval

Offline LangSmith eval for the Open SWE **agent** graph — the coding loop the
reviewer eval does not cover. The reviewer eval answers "does it find the right
issues"; this one answers "does it land the right fix".

Scoring is deterministic: an instance is **resolved** when every `fail_to_pass`
test passes and every `pass_to_pass` test still passes after applying the
agent's patch (plus the benchmark's `test_patch`) to a clean checkout at
`base_commit`. That is the SWE-bench definition, so resolve rates are directly
comparable to published leaderboards. No LLM judge is involved.

## Layout

```
evals/agent/
├── config.toml        # default benchmark run config
├── build_dataset.py   # instances JSONL → LangSmith dataset
├── target.py          # invokes the agent graph over langgraph_sdk
├── judge.py           # applies the patch, runs the tests, scores
└── run_eval.py        # client.aevaluate entrypoint
```

Instances live outside this directory as JSONL (see below); keep large files out
of the repo and point `instances_path` at them.

## Instance schema

One JSON object per line:

```json
{
  "instance_id": "django__django-11099",
  "repo": "django/django",
  "base_commit": "2728b0e0f5eb52c0e0f0e0e0e0e0e0e0e0e0e0e0",
  "problem_statement": "…the issue text the agent sees…",
  "patch": "…gold patch (grading only, never shown to the agent)…",
  "test_patch": "…the benchmark's test patch…",
  "fail_to_pass": ["tests/xyz/test_abc.py::test_def"],
  "pass_to_pass": ["tests/xyz/test_abc.py::test_other"],
  "install_commands": ["pip install -e ."],
  "test_command": "pytest"
}
```

`instance_id`, `repo`, `base_commit`, `problem_statement`, and `fail_to_pass`
are required. `pass_to_pass`, `test_patch`, `patch`, `install_commands`, and
`test_command` are optional — an instance without `pass_to_pass` can only score
regressions on the tests it names. `clone_url` is an optional git remote both
the agent and the judge clone from — set it when github.com is unreachable from
your runners (a mirror) or the repo lives on a self-hosted Git host; it defaults
to `https://github.com/<repo>.git`.

Only the four identity fields go into LangSmith dataset `inputs`; everything
else is `outputs`, which the target never reads. The agent cannot see how it is
being scored.

## 1. Build the dataset

```bash
# Dry run — writes dataset_dryrun.json without uploading
uv run python -m evals.agent.build_dataset --dry-run

# Upload for real
uv run python -m evals.agent.build_dataset --dataset-name openswe-agent-v1
```

`evals/agent/instances/smoke.jsonl` is a small self-contained example instance
(a `zero_pad` task against `pallets/itsdangerous` at a pinned base commit) you
can upload and run end to end:

```bash
uv run python -m evals.agent.build_dataset \
    --instances evals/agent/instances/smoke.jsonl \
    --dataset-name openswe-agent-smoke
```

**Validate a new instance before benchmarking against it.** For any instance
you author, generate the reference solution as a patch and score it through
the same judge path (`_evaluate_instance` with the gold patch, real clone,
real install, real pytest). A gold patch that does not resolve means the
instance — its test ids, its install commands, or its base commit — is broken,
and every agent run against it is noise. This is also how `smoke.jsonl` was
built.

To build instances from the HuggingFace SWE-bench datasets, export the split to
JSONL with the fields above, e.g.:

```python
from datasets import load_dataset

ds = load_dataset("SWE-bench/SWE-bench_Verified", split="test")
with open("swe_bench_verified.jsonl", "w") as f:
    for row in ds:
        f.write(
            json.dumps(
                {
                    "instance_id": row["instance_id"],
                    "repo": row["repo"],
                    "base_commit": row["base_commit"],
                    "problem_statement": row["problem_statement"],
                    "patch": row["patch"],
                    "test_patch": row["test_patch"],
                    "fail_to_pass": row["FAIL_TO_PASS"],
                    "pass_to_pass": row["PASS_TO_PASS"],
                    "install_commands": spec_install_commands(row),  # from the spec, see note below
                }
            )
            + "\n"
        )
```

SWE-bench's per-repo install specs live in the harness's `specs` directory; map
them into `install_commands` (a plain `pip install -e .` is enough for many
repos but not for django, sympy, or scikit-learn).

## 2. Run the eval

```bash
# Smoke-test with 3 instances first
uv run python -m evals.agent.run_eval --limit 3

# Full run
uv run python -m evals.agent.run_eval
```

Prerequisites:

- `LANGSMITH_API_KEY` set in your env.
- A running agent graph (local `langgraph dev` or a deployment) with
  `AGENT_ASSISTANT_ID` pointing at it. Defaults to assistant `agent` on
  `http://localhost:2024`.
- The deployment's GitHub App installed on every benchmark repo — the sandbox
  clones through it. Read access is enough; the eval never pushes.
- The **judge** runs on the machine invoking `run_eval` (not in the
  deployment): it needs `git`, `python`, network access to github.com, and
  enough disk for one checkout per concurrent instance. Run it on a durable
  runner, the same way the reviewer eval does — a deploy or container recycle
  can kill a long run.
- The judge spawns `python -m pytest` for the repo's tests. Set
  `AGENT_EVAL_PYTHON` when bare `python` on PATH is not the interpreter you
  mean (Windows Store stubs, a specific venv); it defaults to `python`. Its
  directory is prepended to PATH for the repo's `install_commands`, so a bare
  `pip install` resolves to the same environment.
- The agent graph authenticates to GitHub with the deployment's App by
  default. Against a server whose `SANDBOX_TYPE` is `local` and which has no
  App credentials, pass `--run-source desktop` (or set
  `AGENT_EVAL_RUN_SOURCE=desktop`): the desktop surface is the product's
  local-execution path and skips App token resolution. Do not use it with a
  cloud sandbox provider — those still need the token. The desktop surface also
  works in one allowlisted directory, so `--run-source desktop` requires
  `--local-project-dir` (`AGENT_EVAL_LOCAL_PROJECT_DIR`) pointing at a path
  listed in the server's `OPEN_SWE_LOCAL_PROJECTS_FILE` or living under its
  `OPEN_SWE_LOCAL_WORKTREES_DIR`.

Eval traces are routed to the `open-swe-evals` LangSmith project
(`langsmith_project` in `config.toml`) so they stay out of the deployment's
production tracing project. Threads are deleted after the experiment; sandboxes
are reclaimed by the provider TTL.

## What gets scored

Per instance (`judge_resolution`):

| Feedback key | Meaning |
|---|---|
| `resolution` | 1 when resolved, else 0 — the headline metric |
| `patch_applied` | the agent's diff applied cleanly at `base_commit` |
| `fail_to_pass_rate` | fraction of the instance's target tests now passing |
| `pass_to_pass_rate` | fraction of previously-passing tests still passing |
| `error` | checkout/install/test-patch failure, when the run could not be scored |
| `steps`, `wall_clock_seconds` | model turns and wall clock, from the target |

Across the experiment (`aggregate_resolution`): `resolve_rate`,
`patch_applied_rate`, `error_rate`, mean per-test rates, and median
steps/wall-clock. Token cost per run is not computed here — read it from the
`open-swe-evals` tracing project or from the `run.completed` analytics events
(`input_tokens` / `output_tokens`) once the run finishes.

## How the target collects the patch

The agent is told to fix the issue and explicitly **not** to open a pull
request — delivery writes to the upstream repo, which an eval must not do. When
the run finishes, the target sends one follow-up message on the same thread:
"run `git add -A`, then output `git diff --cached`". The thread's sandbox still
holds the working tree, so the diff comes back as the final message. The judge
extracts it with `extract_patch` (fenced blocks preferred, raw diffs accepted,
prose rejected as a no-change run).

This is deliberately a prompt-level contract. The cleaner long-term fix is an
`agent_eval` config mode in the graph that suppresses delivery in-graph and
returns the patch as the run result — tracked as a follow-up, out of scope for
this scaffold.

## Known limitations

- Per-test attribution comes from `pytest -rA` short-summary lines; test ids
  must match what pytest prints (run from the repo root). Parametrized ids with
  brackets are supported.
- The judge assumes pytest. Instances whose repos need another runner should
  set `test_command` — only `pytest` is wired up today.
- `install_commands` run with a shell on a POSIX runner; Windows is not
  supported by the judge.
- LangSmith dataset examples are frozen at upload time — upstream drift cannot
  invalidate a run, but `base_commit` must stay reachable on github.com.
- **A Windows local sandbox degrades the agent, not the harness.** The whole
  pipeline runs on Windows (verified end to end with `smoke.jsonl`), but the
  sandbox's command surface is POSIX: `rm`, `head`, `tail`, `grep`, `pwd`, and
  `cd /D:` all fail under `cmd.exe`, and the file tools reject Windows absolute
  paths. A 9B local model fought the shell for 35 turns without landing a
  checkout and scored zero — a quantified capability finding, not a harness
  failure. Run scored benchmarks on a Linux runner or a Linux sandbox provider;
  use a Windows local sandbox only to smoke-test the pipeline itself.
