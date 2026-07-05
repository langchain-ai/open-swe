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

## 2. Reconcile against the current prompt

The current `custom_prompt` is the starting point — you are editing it, not rewriting
from scratch. Read it (it is summarized for you / available via the dashboard record).
Keep what still holds, strengthen rules the outcomes confirm, and remove or soften rules
the outcomes contradict.

Stay aligned with the reviewer-agent themes in the system prompt.

### Optional light `gh` top-up (non-empty outcomes only)

Only when `read_finding_outcomes` returned at least one `confirmed` or `dismissed`
entry, you MAY do a **light** `gh` top-up (`GH_TOKEN=dummy gh ...`) to confirm a
pattern surfaced by those outcomes. Outcomes are the primary signal — do not re-run
a full PR crawl. If outcomes were empty, skip this subsection entirely; see the
empty-outcomes short-circuit in section 3.

## 3. Save

**Empty-outcomes short-circuit.** If `read_finding_outcomes` returned
`confirmed: 0` AND `dismissed: 0`, STOP the investigation immediately. Do NOT
clone the repo, do NOT run `gh pr list` / `gh pr diff` / `git show` / `gh repo
clone`, do NOT read `CLAUDE.md` or any other repo files. Call
`save_review_style_prompt` once with the existing `custom_prompt` unchanged and
an `analysis_summary` that names the empty-outcomes state (e.g. "0 confirmed,
0 dismissed since last cycle; preserved prior prompt verbatim"). This is a
nightly cron; repos with no new outcomes must exit in under 10 steps.

Otherwise (non-empty outcomes), call `save_review_style_prompt` once with the
refined `custom_prompt` (400–1200 words), an `analysis_summary` that names what
changed this cycle (e.g. "promoted N-pattern after 3 confirmed fixes; dropped
M-pattern after repeated dismissals"), and the `top_reviewers` / counts you have.
