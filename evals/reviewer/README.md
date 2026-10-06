# Reviewer Eval

Offline LangSmith eval for the Open SWE Reviewer graph against the 50 PRs and
173 categorized golden comments from `withmartian/code-review-benchmark`
(`offline/golden_comments` at `e616e84`, the 2026-08 golden refresh). The
`openswe-reviewer-v1` dataset holds the earlier 136 uncategorized goldens;
scores across the two datasets are not comparable.

## Layout

```
evals/reviewer/
├── golden_comments/      # 50 PRs × golden comments (copied from martian benchmark)
├── build_dataset.py      # martian JSON → LangSmith dataset (resolves SHAs via gh)
├── config.toml           # default benchmark run config
├── costs.py              # per-PR / per-run LLM cost from the reviewer's traces
├── judge.py              # claude-opus-4-5 pairwise match evaluator + aggregate
├── target.py             # invokes the reviewer graph over langgraph_sdk
├── store_reporter.py     # publishes live progress to the dashboard store record
└── run_eval.py           # client.aevaluate entrypoint
```

## Prerequisites

- `LANGSMITH_API_KEY` set in your env.
- `gh` authenticated (`gh auth status`) — needed for `build_dataset.py`.
- A LangSmith key with `gateway:invoke` in `LANGSMITH_GATEWAY_API_KEY` (or
  `LANGSMITH_API_KEY`) — the judge runs `claude-opus-4-5` through the LangSmith
  LLM Gateway, like the reviewer's models.
- A running reviewer graph (local `langgraph dev` or deployed assistant id) with
  `REVIEWER_ASSISTANT_ID` env var pointing at it. Defaults to assistant `reviewer`
  on `http://localhost:2024`.

## 1. Build the dataset (once)

```bash
# Dry run — writes evals/reviewer/dataset_dryrun.json without uploading
uv run python -m evals.reviewer.build_dataset --dry-run

# Upload for real
uv run python -m evals.reviewer.build_dataset --dataset-name openswe-reviewer-v2
```

Each example carries: `repo`, `pr_number`, `pr_url`, `base_sha`, `head_sha`,
`base_ref`, `head_ref`, `pr_title`. The dataset is frozen at upload time —
upstream PR drift can't invalidate it.

## 2. Run the eval

The reviewer graph must be running and accept the benchmark message/config
input. Eval runs record findings with `add_finding` and finish with
`publish_review`, which persists the exact ordered publication snapshot scored
by the harness.

```bash
uv run python -m evals.reviewer.run_eval
```

`max_concurrency` defaults to 10, matching the `--n-jobs-per-worker 10` that
`make dev` starts the local server with; each PR is one server run, so a higher
eval concurrency only queues on the server.

Smoke-test with 3 PRs first:

```bash
uv run python -m evals.reviewer.run_eval --limit 3
```

### Codex reviewer baseline

`--target codex` scores `codex exec review` on the same dataset and judge. Each
PR is checked out at its head SHA from a cached blobless clone
(`~/.cache/openswe-codex-eval`, override with `CODEX_EVAL_CACHE_DIR`) and
reviewed against its base SHA. Findings come from Codex's structured review
output (P0–P3 map to critical–low). Requires the `codex` CLI on `PATH`. Model
calls go through the LangSmith gateway when it is enabled, otherwise through
Codex's own auth (`CODEX_API_KEY` or `codex login`). Set `CODEX_EVAL_MODEL`
and `CODEX_EVAL_REASONING_EFFORT` to pin them; `--model-id`,
`--reasoning-effort`, and cost collection apply only to the Open SWE target.

```bash
uv run python -m evals.reviewer.run_eval --target codex --experiment-prefix codex-review
```

### From the dashboard (recommended for full runs)

Admins start a run from **Evals** in the sidebar (`/admin/evals`): set the
dataset, run name, reviewer model and effort, concurrency, limit, score mode and
severity threshold, then **Start eval**. The deployment boots a LangSmith
sandbox at its own commit, runs `run_eval` there against itself, and the sandbox
stops itself when the eval finishes. The deployment's LangSmith keys are
injected by the sandbox proxy; they never enter the sandbox.

Progress, the log tail and the LangSmith experiment link stream to the same
page. The full log is at `/root/reviewer-eval.log` in the sandbox named on the
page. Stop that sandbox to cancel; the dashboard flips the run to `failed`
within ~60s.

### Tracing project

Eval traces are routed to the **`open-swe-evals`** LangSmith project (set via
`langsmith_project` in `config.toml`, default `open-swe-evals`) so they stay out
of the deployment's production tracing project. The admin-triggered run forces
the same project via the `LANGSMITH_PROJECT` env var; override the default with
`EVAL_LANGSMITH_PROJECT`.

The runner reads benchmark settings from `evals/reviewer/config.toml`. Set the
deployment URL there (or leave it blank to use `LANGGRAPH_URL` / local dev).
The target sets `reviewer_eval` for every run, so `publish_review` does not post
to GitHub.

## Scoring

The judge prompt and model (`claude-opus-4-5`) match upstream. Unprefixed
metrics count every golden (upstream's "all" profile). `strict_*` and `core_*`
follow upstream's category profiles: a candidate that matches a golden outside
the profile is neither a true nor a false positive, and only in-profile goldens
count as misses. Upstream's leaderboard headline is **`core_micro_f2`**.

| Profile | Categories | Goldens |
|---|---|---|
| `strict` | bug, security, concurrency, data, api | 139 |
| `core` | strict + perf, test_gap, doc_defect | 158 |
| all (unprefixed) | core + style, speculative | 173 |

Upstream also runs an LLM step that groups a tool's duplicate comments before
judging. It is not ported: the reviewer's published findings are already
deduplicated, and the judge drops exact duplicates.

## Prompts in eval runs

Eval runs score the stock reviewer. They skip workspace org guidelines,
per-repo review style prompts, the API-standards skill, approval policy,
historical PR comments, and the walkthrough. They keep the repo's own
AGENTS.md and `resources/prompts/reviewer/eval.md`.

## Cost

The reviewer traces into the server's LangSmith project, not the experiment,
so `run_eval` reads each reviewer thread's cost from that project when the
experiment finishes. It records `cost_usd` and `total_tokens` feedback per
example and writes `cost_total_usd`, `cost_mean_per_pr_usd`,
`cost_median_per_pr_usd`, `cost_max_per_pr_usd`, and `cost_priced_prs` into
the experiment metadata. The project defaults to `LANGSMITH_PROJECT` from
`.env`, which is what local `make dev` traces into; pass
`--reviewer-langsmith-project` for a deployment. Costs cover LLM calls only,
not sandbox time. LangSmith can lag while pricing traces; re-run the lookup
for an experiment with:

```bash
uv run python -m evals.reviewer.costs --experiment <experiment-name>
```

By default the judge scores the exact final `surfaced_findings` snapshot,
including only renderable findings selected by `publish_review`. Set
`score_mode = "all_findings"` only to diagnose deduplicated `add_finding`
calls before publication.

`model_id` and `reasoning_effort` in the config are passed to the reviewer run,
so isolated benchmark deployments can test a specific model/effort without
changing deployment-wide defaults.

## Notes

- No GitHub forks needed — both upstream repos and martian's benchmark forks
  (`ai-code-review-evaluation/*`) are public.
- `judge_match` evaluates the full deduplicated
  `n_candidates × n_goldens` matrix so matching is order-independent and its
  reasoning remains auditable in LangSmith.
