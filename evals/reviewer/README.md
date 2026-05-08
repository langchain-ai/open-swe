# Reviewer Eval

Offline LangSmith eval for the Open SWE Reviewer graph against the 50 PRs from
`withmartian/code-review-benchmark`. See `REVIEWER_EVAL_PLAN.md` at the repo
root for the full design.

## Layout

```
evals/reviewer/
├── golden_comments/      # 50 PRs × golden comments (copied from martian benchmark)
├── build_dataset.py      # martian JSON → LangSmith dataset (resolves SHAs via gh)
├── judge.py              # claude-opus-4-5 pairwise match evaluator + aggregate
├── target.py             # invokes the reviewer graph over langgraph_sdk
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

The reviewer graph must be running and accept a `pr` input matching the
example schema, and must emit a `submit_review` tool call (or set
`state["review"]["comments"]`) with `[{file, line, severity, body}, ...]`.

```bash
uv run python -m evals.reviewer.run_eval \
    --experiment-prefix openswe-reviewer-baseline \
    --max-concurrency 5
```

Smoke-test with 3 PRs first:

```bash
uv run python -m evals.reviewer.run_eval --limit 3
```

## Comparing against Devin Review

Both tools are scored on the same 50 PRs with the same judge model
(`claude-opus-4-5`) and the same judge prompt (verbatim from martian
`step3_judge_comments.py`). Pull martian's published Devin numbers from their
dashboard and compare against the LangSmith experiment's `micro_*` /
`macro_*` summary metrics.

## Notes

- No GitHub forks needed — both upstream repos and martian's benchmark forks
  (`ai-code-review-evaluation/*`) are public.
- `judge_match` charges judge LLM tokens proportional to
  `n_candidates × n_goldens` per example. For 50 PRs with ~3 goldens each and
  agents emitting ~10 candidates, expect ~1500 judge calls per experiment.
