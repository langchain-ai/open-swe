---
name: continual-learning
description: Nightly refinement of an existing per-repo review-style prompt using this reviewer's own finding outcomes. Read confirmed (resolved-by-commit / thumbs-up) and dismissed (thumbs-down) findings, promote the bug patterns the team actually fixes, demote the false-positive patterns, reconcile against the current prompt, and save the refined version. Use this once outcomes exist; use bootstrap-repo-analysis for a cold-start repo.
---

# Continual learning

You are **refining** the existing review-style prompt for the repository named in the
system prompt, using outcomes the reviewer has accrued since the last run. The goal is
to raise recall (catch more real bugs) without hurting precision (stop repeating
dismissed ones).

## 1. Read outcomes first

Call `read_finding_outcomes` once. It returns this repo's past findings split into:

- `confirmed` — resolved by a follow-up commit or 👍'd. These are **real** bug patterns
  this team fixes. Promote the recurring ones into the prompt's "hunt for" guidance,
  quoting the `file`/`diff_hunk` context so the rule stays concrete.
- `dismissed` — dismissed or 👎'd. These are **false-positive** patterns. Add the
  recurring ones to the prompt's "do not flag" section so the reviewer stops repeating
  them.

Look for repetition, not one-offs. A single dismissed finding is noise; the same class
dismissed several times is a rule.

### How to retrieve outcomes without burning the run

The tool returns **at most `limit` rows total** across `confirmed` + `dismissed`, default
60. The cap is applied in arbitrary dataset order, so once it is hit the remaining
outcomes are dropped silently — there is no "more results" signal beyond the `counts`.

Every row embeds the finding's full `description` and `diff_hunk`, so a full 60-row
result commonly exceeds 190,000 characters. When that happens the sandbox offloads the
result to a file and the tool message gives you a path like
`/large_tool_results/call_<hash>` instead of the rows.

If the result is offloaded:

- Read the artifact with `read_file`, using explicit `offset`/`limit` paging, and work
  from the `counts` summary plus the pages you actually need.
- Do **not** shell out to inline `python -c` / `jq` to extract the JSON, do **not** write
  `/tmp` scratch files, and do **not** dispatch a `task` subagent to parse it. Those
  workarounds cost several times what paged reads cost and are not needed here.
- You are looking for recurring pattern classes, not an exhaustive index. A few pages of
  rows per bucket is normally enough to decide what to promote and what to demote.

If `counts.confirmed + counts.dismissed` equals the `limit` you passed (60 by default),
the cap was reached and you are seeing a truncated, arbitrarily ordered slice. Say so in
`analysis_summary` in step 3 so the truncation is disclosed rather than hidden.

## 2. Reconcile against the current prompt

The current `custom_prompt` is the starting point — you are editing it, not rewriting
from scratch. Read it (it is summarized for you / available via the dashboard record).
Keep what still holds, strengthen rules the outcomes confirm, and remove or soften rules
the outcomes contradict. Optionally do a **light** `gh` top-up
(`GH_TOKEN=dummy gh ...`) to confirm a pattern, but outcomes are the primary signal — do
not re-run a full PR crawl.

Stay aligned with the reviewer-agent themes in the system prompt.

## 3. Save

Call `save_review_style_prompt` once with the refined `custom_prompt` (400–1200 words),
an `analysis_summary` that names what changed this cycle (e.g. "promoted N-pattern after
3 confirmed fixes; dropped M-pattern after repeated dismissals"), and the
`top_reviewers` / counts you have. If outcomes were empty and nothing changed, say so in
`analysis_summary` and re-save the existing prompt unchanged rather than degrading it.
