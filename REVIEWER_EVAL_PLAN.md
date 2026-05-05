# Goal

Score Open SWE Reviewer on the 50 PRs from the martian offline benchmark, in conditions close to a real PR review, and compare against Devin Review's published numbers. Manual run, baseline only.

## Dataset

Import the 50 entries from `withmartian/code-review-benchmark` `offline/golden_comments/*.json` into a LangSmith dataset (`openswe-reviewer-v1`).

For each PR, enrich the example up-front via `gh pr view --json` so the example carries everything needed to reproduce the PR's state:

```json
{
  "inputs": {
    "repo": "getsentry/sentry",
    "fork_repo": "<your-org>/sentry",
    "pr_number": 12345,
    "base_sha": "<main-at-PR-open-time>",
    "head_sha": "<PR-tip>",
    "pr_title": "...",
    "original_url": "<https://github.com/getsentry/sentry/pull/12345>"
  },
  "outputs": {
    "golden_comments": [
      {"comment": "...", "severity": "High"}
    ]
  }
}
```

`base_sha` must be the **PR's base commit at open time**, not today's main. `gh pr view <n> --json baseRefOid,headRefOid` returns these — recover from upstream once and freeze them in the dataset. From this point the dataset is self-contained and reproducible regardless of upstream activity.

## Repo setup

Fork the 5 upstream repos (sentry, grafana, [cal.com](http://cal.com/), discourse, keycloak) into your personal org once. The agent clones from your forks rather than upstream — gives stable targets, isolates from upstream rate limits, no risk of upstream force-pushes invalidating SHAs.

No need to fork the 50 PRs themselves — we're not relying on the GitHub-App-review flow. The PR's content is reconstructed from `base_sha` + the diff fetched from upstream once at dataset-build time (or fetched on demand via `git fetch origin pull/<n>/head`).

## Target function

```python
async def review_pr(inputs: dict) -> dict:
    thread = await client.threads.create()
    run = await client.runs.wait(
        thread["thread_id"],
        assistant_id="reviewer",
        input={"pr_inputs": inputs},
    )
    return {"comments": extract_review_comments(run)}
```

Inside the reviewer graph, the first agent step (or a pre-agent middleware) runs in the sandbox:

```bash
git clone --depth=200 <https://github.com/><fork_repo>.git /workspace
cd /workspace
git fetch origin pull/<pr_number>/head:pr
git checkout <base_sha>
git merge --no-commit --no-ff pr   # or: leave as two refs and let agent diff
```

This puts the sandbox in the same state a human reviewer would see when the PR was opened: main at the base SHA, plus the PR's changes applied/available. Agent then uses its normal tool set (`read_file`, `glob`, `grep`, `gh pr diff`) over that working tree.

The reviewer must emit structured comments. Cleanest path: a `submit_review` tool whose args (`[{file, line, severity, body}, ...]`) become the run output. More reliable than parsing the final assistant message.

## Evaluators

Because `submit_review` already returns one structured entry per issue (`{file, line, severity, body}`), there's no prose to split and no summary-vs-inline duplication to collapse. We skip martian's `step2_extract_comments` and `step2_5_dedup_candidates` and feed the agent's list straight into the judge. Port the judge prompt verbatim from `step3_judge_comments.py` so scores stay comparable to Devin's published numbers.

- **Per-example evaluator `judge_match`**: receives the agent's `comments` (from `run.outputs`) and the `golden_comments` (from `example.outputs`). For each `(candidate, golden)` pair, asks the judge LLM "do these describe the same underlying issue?". Tallies TP / FP / FN → returns `{precision, recall, f1, tp, fp, fn}` per example.
- **Summary evaluator `aggregate_pr`**: micro- and macro-averaged precision/recall across the 50 examples.

Judge model: **`claude-opus-4-5`** — matches the model martian used to score Devin Review.

## Run

```python
client.evaluate(
    review_pr,
    data="openswe-reviewer-v1",
    evaluators=[judge_match],
    summary_evaluators=[aggregate_pr],
    experiment_prefix="openswe-reviewer-baseline",
    max_concurrency=5,
)
```

`max_concurrency=5` keeps sandbox provider load reasonable. Full run: ~50 examples × 5–15 min/PR ÷ 5 = ~1–2.5h wall.

## Comparison

Devin's published numbers come from the same 50 goldens + same judge model + same prompts. As long as we hold those three constant, the LangSmith experiment's aggregate precision/recall is directly comparable. Drop both into a side-by-side table; LangSmith's experiment-compare view also works if you import Devin's results as a separate experiment over the same dataset.