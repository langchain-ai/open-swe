# Reviewer Eval

Offline LangSmith eval for the Open SWE Reviewer graph against the 50 PRs and
136 reference findings from `withmartian/code-review-benchmark`. Examples have
1–6 references (mean 2.72).

## Layout

```
evals/reviewer/
├── golden_comments/      # 50 PRs × golden comments (copied from martian benchmark)
├── build_dataset.py      # martian JSON → LangSmith dataset (resolves SHAs via gh)
├── config.toml           # default benchmark run config
├── judge.py              # claude-opus-4-5 pairwise match evaluator + aggregate
├── target.py             # invokes the reviewer graph over langgraph_sdk
├── store_reporter.py     # publishes live progress to the dashboard store record
└── run_eval.py           # client.aevaluate entrypoint
```

## Prerequisites

- `LANGSMITH_API_KEY` set in your env.
- `gh` authenticated (`gh auth status`) — needed for `build_dataset.py`.
- `ANTHROPIC_API_KEY` set — judge runs `claude-opus-4-5`.
- A running reviewer graph (local `langgraph dev` or deployed assistant id) with
  `REVIEWER_ASSISTANT_ID` env var pointing at it. Defaults to assistant `reviewer`
  on `http://localhost:2024`.

## 1. Build the dataset (once)

```bash
# Dry run — writes evals/reviewer/dataset_dryrun.json without uploading
uv run python -m evals.reviewer.build_dataset --dry-run

# Upload for real
uv run python -m evals.reviewer.build_dataset --dataset-name openswe-reviewer-v1
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

Smoke-test with 3 PRs first:

```bash
uv run python -m evals.reviewer.run_eval --limit 3
```

### From the GitHub Action (recommended for full runs)

Trigger the **Reviewer eval** workflow (`.github/workflows/reviewer_eval.yml`)
from the Actions UI or `gh workflow run reviewer_eval.yml --ref prod -f limit=3`.
Run it on the **prod** branch so the harness/judge match the deployed reviewer it
scores. Running it on a durable runner (instead of inside the serving deployment)
means a deploy or container recycle can't kill a long run.

The Action sets `REVIEWER_EVAL_REPORT_STORE=1`, so `run_eval` publishes live
status/progress/logs to the LangGraph store record the dashboard reads — watch it
at **Admin → Reviewer eval** (`/admin/evals`), which is now a read-only progress
view (status, `completed / total`, log tail, LangSmith experiment link, and a link
back to the GitHub run). If the Action is cancelled/killed, the heartbeat goes
stale and the dashboard flips the run to `failed` within ~60s.

Required repository config:

- secrets: `LANGSMITH_API_KEY`, `ANTHROPIC_API_KEY` (the judge runs in-process;
  reviewer-model keys are **not** needed — the reviewer runs in the deployment).
- secret or var: `LANGGRAPH_URL` — the deployment URL the eval drives and reports to.

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

## Per-repo review style prompts

At runtime the reviewer loads a custom style guide from LangGraph Store when
`configurable.repo` is set (`owner` + `name` → store key `owner/name`). This
applies to **eval runs too**, as long as a completed style profile exists for
that repo.

The Martian benchmark uses these upstream repos (10 PRs each):

- `getsentry/sentry`
- `keycloak/keycloak`
- `grafana/grafana`
- `discourse/discourse`
- `calcom/cal.com`

Before scoring with repo-specific styles, run **Review styles** analysis in the
dashboard for each repo (or copy prompts into store). Re-run `make dev` so the
reviewer graph sees the same store.

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
