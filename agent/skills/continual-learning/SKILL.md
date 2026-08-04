---
name: continual-learning
description: Nightly refinement of an existing per-repo review-style prompt using this reviewer's own finding outcomes. Read confirmed (resolved-by-commit / thumbs-up) and dismissed (thumbs-down) findings, promote the bug patterns the team actually fixes, demote the false-positive patterns, reconcile against the current prompt, and save the refined version. Use this once outcomes exist; use bootstrap-repo-analysis for a cold-start repo.
---

# Continual learning

You are writing the review-style prompt for the repository named in the system prompt,
using outcomes the reviewer has accrued since the last run. The goal is to raise recall
(catch more real bugs) without hurting precision (stop repeating dismissed ones). You
cannot read the prompt currently stored for the repo — see step 2.

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

## 2. Write a complete standalone prompt

The repository's previous `custom_prompt` is **not available to you**. Your only tools are
`read_finding_outcomes` and `save_review_style_prompt`, and `read_finding_outcomes`
returns only `{ok, repo, counts, confirmed, dismissed}` — no prompt text. Do not claim or
assume you have seen the stored prompt.

`save_review_style_prompt` is a **full replacement**: whatever you save becomes the entire
stored prompt. So synthesize a complete, self-contained prompt from the outcomes above
plus the reviewer-agent themes already given in your system prompt — never a patch, a
delta, or a fragment that only makes sense next to an earlier version.

Optionally do a **light** `gh` top-up (`GH_TOKEN=dummy gh ...`) to confirm a pattern, but
outcomes are the primary signal — do not re-run a full PR crawl.

Stay aligned with the reviewer-agent themes in the system prompt.

## 3. Save

Call `save_review_style_prompt` once with the full `custom_prompt` (400–1200 words), an
`analysis_summary` grounded **only** in what the current outcomes support (e.g. "promoted
N-pattern after 3 confirmed fixes; added M-pattern to do-not-flag after repeated
dismissals"), and the `top_reviewers` / counts you have. Never describe the save as
reconciled against, merged with, or preserving a previous prompt — you never read one, so
any such claim is false.

If `read_finding_outcomes` came back empty (or failed), do **not** call
`save_review_style_prompt` at all — a save would overwrite the accumulated prompt with a
weaker one built from no evidence. Instead end the run with a final message saying
outcomes were empty and the stored prompt was left untouched.
