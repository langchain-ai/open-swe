# Agent Eval

Offline LangSmith eval for the Open SWE **agent** graph — the coding loop the
reviewer eval does not cover. The reviewer eval answers "does it find the right
issues"; this one answers "does it land the right fix".

Scoring is deterministic: an instance is **resolved** when every `fail_to_pass`
test passes and every `pass_to_pass` test still passes after applying the
agent's patch (plus the benchmark's `test_patch`) to a clean checkout at
`base_commit`. This follows SWE-bench-style test-based scoring. Comparison with
published scores requires the same instances, environment, and execution policy;
running a small custom task set does not establish SWE-bench performance. No LLM
judge is involved.

## Layout

```
evals/agent/
├── config.toml        # default benchmark run config
├── build_dataset.py   # instances JSONL → LangSmith dataset
├── target.py          # invokes the agent graph over langgraph_sdk
├── judge.py           # applies the patch, runs the tests, scores
├── models.py          # typed task, target, and failure-stage contracts
├── report.py          # JSONL evidence, summary JSON, and Markdown report
├── dataset.py         # strict task loading and content fingerprints
├── run_local.py       # local JSONL experiments without dataset upload
├── compare.py         # paired comparison of complete model experiments
├── regressions.py     # freeze selected cases and regrade stored predictions
└── run_eval.py        # client.aevaluate entrypoint
```

Instances live as JSONL (see below). Keep large benchmark exports out of the
repo and point `instances_path` at them; the checked-in `instances/` folder only
holds the small self-contained sets `smoke.jsonl` and `long_context.jsonl`.

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
uv run python -m evals.agent.run_eval --limit 3 --output-dir artifacts/agent-eval/baseline

# Full run
uv run python -m evals.agent.run_eval
```

Prerequisites:

- `LANGSMITH_API_KEY` set in your env.
- A running agent graph (local `langgraph dev` or a deployment) with
  `AGENT_ASSISTANT_ID` pointing at it. Defaults to assistant `agent` on
  `http://localhost:2024`.
- A sandbox that can read each benchmark repository through its `clone_url` or
  default GitHub URL. Provision read-only repository credentials when required.
  Evaluation mode omits product delivery tools; shell permissions still follow
  the sandbox policy.
- The **judge** runs on the machine invoking `run_eval` (not in the
  deployment): it needs `git`, `python`, network access to github.com, and
  enough disk for one checkout per concurrent instance. Run it on a durable
  runner, the same way the reviewer eval does — a deploy or container recycle
  can kill a long run.
- The judge spawns pytest for the repo's tests. Set `AGENT_EVAL_PYTHON` to
  select a specific interpreter; the default is the interpreter running the
  evaluator (`sys.executable`), independent of bare `python` on PATH. Its
  directory is prepended to PATH for the repo's `install_commands`, so a bare
  `pip install` resolves to the same environment.
- Cloud runs need the deployment's workspace and sandbox configuration. Against
  a local server with no App credentials, pass `--run-source desktop` (or set
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

## Local experiments and paired comparisons

The local runner uses the same target and judge without uploading a dataset or
creating a LangSmith experiment. It defaults tracing to disabled unless explicitly
configured. Model mode still requires a reachable agent graph and model provider.
It uses `config.toml` plus the same environment overrides as `run_eval`.

```bash
uv run python -m evals.agent.run_local \
    --instances evals/agent/instances/smoke.jsonl \
    --output-dir artifacts/agent-eval/baseline

# Change the intended model/prompt setting, keeping the tasks and judge fixed.
uv run python -m evals.agent.run_local \
    --instances evals/agent/instances/smoke.jsonl \
    --output-dir artifacts/agent-eval/candidate

uv run python -m evals.agent.compare \
    --baseline artifacts/agent-eval/baseline \
    --candidate artifacts/agent-eval/candidate \
    --output-dir artifacts/agent-eval/comparison
```

Each run freezes `tasks.json`, records a SHA-256 of the normalized task inputs
**and grading specifications**, and saves the original `TargetResult` files in
`predictions/`. These grading files are local evaluator evidence; do not expose
the report directory to the agent. Only `EvalInputs` is sent to the target.
Raw predictions allow later diagnosis or regrading through `evaluate_target`
without another model call. Duplicate or malformed source rows fail the run,
including rows beyond `--limit`; invalid data never silently shrinks the task set.
Use a new output directory per experiment; existing directories are refused.

The comparison checks the task snapshots, identities, counts, completion status,
judge code/host policy, and concurrency. It rejects incomplete runs and different
task sets. Improvements and regressions are listed by task ID. Resolution deltas
are percentage points; time, turns, and tokens use per-task paired differences.
Unknown turn/usage values are excluded from the corresponding paired metric and
the number of usable pairs is reported. Execution failures stay in the resolution
denominator. This prevents missing observations from looking like cost savings.

Comparisons also report each run's experimental condition (model and compaction
arm), paired compaction count and token deltas, overflow-triggered compaction
totals, and a baseline-to-candidate failure-stage transition matrix.

### Compaction experiments

[LONG_CONTEXT_TASKS.md](LONG_CONTEXT_TASKS.md) describes the ten-task
`instances/long_context.jsonl` set and the arm protocol. The arm travels through
the eval `configurable`, so only compaction changes between runs:

```bash
AGENT_EVAL_COMPACTION_TRIGGER_TOKENS=16000 \
AGENT_EVAL_COMPACTION_SUMMARY_PROMPT=structured \
uv run python -m evals.agent.run_local \
    --instances evals/agent/instances/long_context.jsonl \
    --output-dir artifacts/agent-eval/structured-1
```

`run_eval` exposes the same settings as `--compaction-trigger-tokens`,
`--compaction-trigger-messages`, `--compaction-trigger-fraction`, and
`--compaction-summary-prompt`. Only one trigger dimension may be set; leaving all
of them unset keeps the model-derived defaults. `trigger_tokens` works for models
without a context-window profile. The structured prompt lives at
`agent/resources/prompts/compaction/structured-summary.md`.

`--mode reference` grades the task's `patch`; `--mode empty` grades no changes.
Both modes skip the agent and mark their reports as having no model predictions.
Reference mode requires every selected task to have a nonempty gold patch.
The comparison rejects these self-check reports. Before benchmarking, verify
reference patches pass and empty patches fail through real Git and pytest.

The pinned smoke task and synthetic judge fixtures verify the harness. They do
not form a representative Agent benchmark. Build a larger task set and repeat
model experiments before claiming success-rate or latency improvements. Record
sandbox images, model versions, and dependency locks alongside the reports;
the local fingerprint does not freeze external infrastructure. See
[LOCAL_RESULTS.md](LOCAL_RESULTS.md) for verified results and resume wording.

## Frozen failure regression

Select cases from a local run and regrade their stored predictions without
calling the model again:

```bash
uv run python -m evals.agent.regressions freeze \
    --run-dir artifacts/agent-eval/baseline \
    --instance-id <instance-id> \
    --output artifacts/selected-cases.json

uv run python -m evals.agent.regressions replay \
    --cases artifacts/selected-cases.json \
    --output-dir artifacts/agent-eval/replay
```

Repeat `--instance-id` to select multiple cases. Freeze records the task and
grading, prediction, expected resolution/failure stage, and source task-set hash.
Replay writes ordinary evidence reports plus `expectations.json`; a changed
resolution or failure stage exits with status 1. Existing output files and
directories are refused. Replays are labeled `regression_replay` and cannot be
used as model-performance comparisons. They regrade stored artifacts, rather
than replaying the model/tool trajectory or retrying a historical provider call.
Repository sources and dependencies must remain available. Review and sanitize
frozen cases before sharing; raw cases are not automatically redacted.

Desktop and evaluation runs also capture `execution_environment`: observed
sandbox OS, shell family, Python, working directory, Git, and probe failures.
The main Agent uses a shared model/tool invocation deadline controlled by
`OPEN_SWE_RUN_TIMEOUT_SECONDS` (default 3600 seconds). See
[AGENT_RELIABILITY.md](../../docs/AGENT_RELIABILITY.md) for cancellation behavior,
configuration, and the boundaries of environment and idempotency guarantees.

## What gets scored

Per instance (`judge_resolution`):

| Result field | Meaning |
|---|---|
| `resolution` | 1 when resolved, else 0 — the headline metric |
| `patch_applied` | the agent's diff applied cleanly at `base_commit` |
| `fail_to_pass_rate` | fraction of the instance's target tests now passing |
| `pass_to_pass_rate` | fraction of previously-passing tests still passing |
| `failure_stage` | the execution or correctness stage that prevented resolution |
| `error` | bounded error summary; complete output is retained in `diagnostic_output` |
| `steps`, `wall_clock_seconds` | model turns and wall clock, from the target |
| `failed_test_ids`, `missing_test_ids` | named tests that failed or never reported an outcome |
| `test_exit_code`, `timed_out` | pytest termination and timeout evidence |
| `phase_seconds` | checkout, installation, patch application, and test durations |
| `execution_environment` | observed sandbox capabilities and environment probe errors |
| `input_tokens`, `output_tokens`, `usage_complete` | observed main-agent message usage and its completeness |
| `compaction` | run-level compaction count, messages/tokens summarized, summary tokens, duration, and overflow count |

Across the experiment (`aggregate_resolution`): `resolve_rate`,
`patch_applied_rate`, `error_rate`, mean per-test rates, and median
steps/wall-clock, per-stage failure rates, and usage coverage. Compaction
metrics are `compaction_rate`, `mean_compactions`, mean summarized/summary
tokens, `compaction_compression_ratio`, `overflow_compaction_rate`, and
`mean_overflow_compactions`. Every evaluated row, including execution failures,
stays in the resolution-rate denominator. Unknown model-turn counts are excluded
from the observed-turn median, rather than counted as zero. Token counts cover
main-agent messages only: subagents and compaction model calls need tracing
data; compaction token values are middleware estimates. Monetary cost is not
estimated here.

The LangSmith `resolution` feedback contains the score, the failure stage as its
value, and structured diagnosis in `evaluator_info`. `--output-dir` additionally
writes `results.jsonl` (including complete command output), `summary.json` (metrics
and run configuration), and `report.md`. A failed experiment writes an incomplete
report; do not treat it as a finished benchmark. Use a fresh output directory for
each experiment. Reports are written before thread cleanup; `--no-cleanup` keeps
threads available for deeper inspection.

Failure stages distinguish `agent_execution`, `patch_collection`,
`invalid_instance`, `checkout`, `install`, `agent_patch`, `test_patch`,
`test_execution`, `missing_outcomes`, `no_patch`, `target_tests`, and `regression`.
The first blocking stage wins; named failed tests remain available when a run
has both target failures and regressions. A nonzero pytest infrastructure exit
cannot produce a successful score even if some passing outcomes were printed.

## How the target collects the patch

The target sends one coding run with a validated `agent_eval` configuration.
Each thread receives a unique checkout-directory name, supplied in the task
prompt. The graph uses an evaluation-specific system prompt, omits product and
integration tools, and skips title generation and desktop branch renaming.
The ordinary execution and filesystem tools remain available for coding.

After the turn, `AgentEvaluationMiddleware` stages the checkout's changes and
collects `git diff --cached --binary <base_commit>`, including changes the agent
already committed and new files. It redirects the patch to a sibling file and
downloads it through the backend file API, avoiding model-output and shell-stdout
limits. The structured `agent_eval_result` is returned as graph state, with no
second model run. A missing checkout, Git error, or failed download is classified
as `patch_collection`, not as an empty implementation. Upgrade the agent server
alongside the runner; older graphs do not return this artifact.

These restrictions suppress product delivery behavior; shell/network access still
follows the sandbox deployment's policy. Use isolated benchmark projects and
read-only credentials rather than a developer's working checkout. Checkouts and
patch files persist with the sandbox; the runner deletes thread records, not
desktop files or provider sandboxes.

## Known limitations

- Per-test attribution comes from `pytest -rA` short-summary lines; test ids
  must match what pytest prints (run from the repo root). Parametrized ids with
  brackets are supported.
- Only pytest is supported. Other `test_command` values are rejected as
  `invalid_instance`, rather than silently executed with pytest.
- `install_commands` run in the host shell. Linux-oriented benchmark setup
  commands still need a POSIX runner; the local Git/pytest checks run on Windows.
- Patch artifacts are capped at 10 MiB. Larger artifacts are collection errors.
- LangSmith dataset examples are frozen at upload time — upstream drift cannot
  invalidate a run, but `base_commit` must stay reachable on github.com.
- The historical Windows smoke run spent 35 model turns on incompatible shell
  commands and paths without producing a fix. Desktop/evaluation runs now
  detect the sandbox environment and supply corresponding command guidance,
  but no new model benchmark has measured the effect. Linux-oriented benchmark
  tasks still require a compatible Linux runner and dependency environment.
