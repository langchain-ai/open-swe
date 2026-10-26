# Long-context compaction task set

`instances/long_context.jsonl` is a ten-task set used to measure conversation
compaction. The experiment forces a token trigger (see below) instead of waiting
for a model's default context fraction, so every arm crosses the threshold inside
a normal coding run. The tasks are ordinary feature additions on small public
repositories; their purpose is to produce realistic exploration, tool output, and
test cycles, not to benchmark model capability against SWE-bench.

## Composition

| Instance | Repository | Base commit | Feature |
|---|---|---|---|
| `longctx__markupsafe-strip-tags` | `pallets/markupsafe` | `4e11127` | `strip_tags` helper |
| `longctx__itsdangerous-strict-base64` | `pallets/itsdangerous` | `672971d` | strict URL-safe base64 decoder |
| `longctx__boltons-truncate-middle` | `mahmoud/boltons` | `4e5faa3` | middle-truncation helper |
| `longctx__funcy-flatten-dict` | `Suor/funcy` | `e8febff` | nested mapping flattening |
| `longctx__more-itertools-value-counts` | `more-itertools/more-itertools` | `4aeb433` | first-seen-order value counts |
| `longctx__click-truncate-text` | `pallets/click` | `2247b35` | width-aware text truncation |
| `longctx__click-shorten-path` | `pallets/click` | `2247b35` | trailing-component path shortening |
| `longctx__idna-is-valid-label` | `kjd/idna` | `7c55529` | boolean label validator |
| `longctx__sortedcontainers-count-between` | `grantjenks/python-sortedcontainers` | `3ac3586` | bounded range count |
| `longctx__tabulate-display-width` | `astanin/python-tabulate` | `268615a` | visible display width |

Each instance carries a gold patch, a test patch, five to seven `fail_to_pass`
tests, two `pass_to_pass` regression tests, and `python -m pip install -e .`.
Test files live at the repository root so repository conftests and autouse
fixture parametrization do not change test IDs. Feature imports are inside each
test function, so a missing symbol is reported as `FAILED` per test ID rather
than a collection `ERROR`.

## Validation

Validated 2026-10-24 through the harness's own judge, with a dedicated Python
3.12 interpreter on Windows:

```bash
AGENT_EVAL_PYTHON=<runner-venv>/Scripts/python.exe \
AGENT_EVAL_TEST_TIMEOUT_SECONDS=600 \
uv run python -m evals.agent.run_local \
    --instances evals/agent/instances/long_context.jsonl \
    --output-dir artifacts/task-validation/reference \
    --mode reference --max-concurrency 1
```

- Reference mode: **10/10 resolved**, `fail_to_pass_rate = pass_to_pass_rate = 1.0`.
  (Re-checked 2026-10-26 on the Windows desktop host: **9/10**, the markupsafe
  task failing at `install`; see Known limitations.)
- Empty mode: **10/10 `no_patch`**, with `pass_to_pass_rate = 1.0`.
- Task-set fingerprint: `b8cf636f6a98eb0d040d8f75027e1349d3d5f358ae07cbac007215705e4fd499`.
- Raw evidence: `artifacts/task-validation/reference/` and
  `artifacts/task-validation/empty/` (gitignored).

The reference and empty checks are evaluator self-checks. They prove the tasks
are gradable; they say nothing about model performance.

An end-to-end mechanism check (2026-10-24, local desktop sandbox, local
`qwen3.5:9b`, `trigger_messages=8`, structured prompt) seeded eleven messages
into a real agent run. Graph state returned:

```json
{"status": "completed", "trigger": "automatic", "cutoff_index": 5,
 "messages_summarized": 5, "summarized_tokens": 32, "summary_tokens": 45,
 "duration_ms": 139417, "count": 1, "overflow_count": 0}
```

This proves the arm reaches the graph, the structured prompt runs, and the
compaction totals are recorded in state. It is an engineering check, not a
model-performance or cost result. A full-task pilot on the local 9B model was
not run to completion before this iteration, so the formal N≥10 ×3 experiments
below are still pending.

## Experiment arms

The compaction arm travels from the runner to the graph through the eval
configurable, so the task set and judge stay fixed while only compaction changes.
Set arm values as environment variables for `run_local` (it takes `config.toml`
and environment overrides, not the `run_eval` CLI flags):

| Variable | Meaning |
|---|---|
| `AGENT_EVAL_COMPACTION_TRIGGER_TOKENS` | Proactive trigger in middleware token estimates |
| `AGENT_EVAL_COMPACTION_TRIGGER_MESSAGES` | Alternative message-count trigger |
| `AGENT_EVAL_COMPACTION_TRIGGER_FRACTION` | Fraction of the model window (needs a model profile) |
| `AGENT_EVAL_COMPACTION_SUMMARY_PROMPT` | `default` (prose) or `structured` |

Only one trigger dimension may be set. `trigger_tokens` is the portable choice
because it works for models without a context-window profile. Small local models
sometimes finish a task in fewer tokens than the planned threshold; if a dry run
does not compact, lower `AGENT_EVAL_COMPACTION_TRIGGER_TOKENS` or switch to
`AGENT_EVAL_COMPACTION_TRIGGER_MESSAGES` (for example `20`) so the arm reliably
crosses the threshold. Report the dimension and value actually used in the
experimental condition.

## Formal run protocol

Fixed conditions for one comparison: task-set fingerprint, model ID, reasoning
effort, sandbox type and workspace, judge interpreter, `max_concurrency`, and
`AGENT_EVAL_TEST_TIMEOUT_SECONDS`.

```bash
# Server (separate terminal): local shell sandbox, desktop surface.
# --no-reload is required on Windows: the reload path uses a selector event
# loop, which cannot spawn the local sandbox's subprocesses.
PYTHONUTF8=1 SANDBOX_TYPE=local \
LOCAL_SANDBOX_ROOT_DIR=$PWD/artifacts/local-eval-project \
OPEN_SWE_LOCAL_PROJECTS_FILE=$PWD/artifacts/local-eval-projects.json \
uv run langgraph dev --no-browser --no-reload --port 2024 \
    --n-jobs-per-worker 10 --allow-blocking
```

```bash
# One arm, one repetition. Repeat with distinct output dirs and a different
# summary-prompt (or trigger) value for the candidate arm.
AGENT_EVAL_PYTHON=$PWD/artifacts/eval-venv/Scripts/python.exe \
AGENT_EVAL_TEST_TIMEOUT_SECONDS=600 \
AGENT_EVAL_MODEL_ID=openai:qwen3.5:9b \
AGENT_EVAL_RUN_SOURCE=desktop \
AGENT_EVAL_LOCAL_PROJECT_DIR=$PWD/artifacts/local-eval-project \
AGENT_EVAL_COMPACTION_TRIGGER_TOKENS=16000 \
AGENT_EVAL_COMPACTION_SUMMARY_PROMPT=default \
uv run python -m evals.agent.run_local \
    --instances evals/agent/instances/long_context.jsonl \
    --output-dir artifacts/agent-eval/longctx-default-1 \
    --max-concurrency 1
```

Run each arm three times. Then compare each baseline repetition with the matching
candidate repetition, or pool the three runs per arm and report every run:

```bash
uv run python -m evals.agent.compare \
    --baseline artifacts/agent-eval/longctx-default-1 \
    --candidate artifacts/agent-eval/longctx-structured-1 \
    --output-dir artifacts/agent-eval/compare-longctx-1
```

The comparison reports `baseline_condition`/`candidate_condition`,
`failure_stage_transitions`, paired compaction counts and token deltas, and
`overflow_compactions`. With ten instances and three repetitions the experiment
meets the N≥10 ×3 bar; do not report a success-rate or cost change from fewer
runs, and never compare runs with different task-set fingerprints.

## Known limitations

- These are custom feature tasks, not SWE-bench instances. Scores are not
  comparable with published benchmarks.
- Every instance pins `clone_url` to the `ghfast.top` GitHub mirror because the
  authoring runner cannot reach github.com directly. `clone_url` is part of the
  task identity and fingerprint: replacing or removing it creates a different
  task set, so re-run the reference/empty checks and take a fresh baseline.
- The local model and Windows shell sandbox are part of the measured condition;
  results do not transfer to a different model or provider.
- The forced trigger is much lower than a production model's default, so
  compaction frequency here is an experiment artifact, not a production rate.
- Token counts are middleware estimates, not billed usage.
- The runner's judge venv is shared across tasks and installs each package
  editable before applying the agent patch. Do not add tasks whose package is a
  dependency of pytest itself (`packaging`, `pluggy`, ...): once the checkout is
  deleted, a dangling install can break later pytest invocations. The judge now
  uninstalls the graded package after each task, so a package the host cannot
  build no longer breaks the tasks that follow it.
- **The markupsafe task is ungradable on a host without MSVC** (2026-10-26).
  `pallets/markupsafe` builds a C speedups extension in `setup.py` and retries a
  plain-Python build in the same process when the first build fails. Under
  setuptools ≥ 77 the retry cannot find the temporary `.egg-info` directory and
  the editable install aborts, so the judge stops at `install` whatever the
  model did. `reference` mode therefore scores 9/10 on this host
  (fingerprint `b8cf636f...`, raw evidence `artifacts/agent-eval/ref-check-2/`).
  The task stays in the set so the fingerprint and denominator do not change;
  arms run by `artifacts/run-compaction-arms.sh` report it as `ungradable`
  instead of failing the arm. With MSVC Build Tools installed, re-run reference
  mode before treating a 10/10 as the validation result.
