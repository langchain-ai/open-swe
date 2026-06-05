---
name: bootstrap-repo-analysis
description: First-time analysis of a repository with no prior reviewer outcomes. Crawl historical merged-PR review feedback with the gh CLI (plus any preloaded samples), extract the team's review norms, and synthesize the initial per-repo review-style prompt. Use this for a cold-start repo; use continual-learning instead once the reviewer has accumulated finding outcomes.
---

# Bootstrap repo analysis

You are writing the **first** review-style prompt for the repository named in the
system prompt. There is no outcomes history yet, so your signal comes entirely from
the repo's own historical PR review feedback. Do not call `read_finding_outcomes` in
this mode — it will be empty.

Always invoke gh as: `GH_TOKEN=dummy gh <command>`.

## 1. Research (required)

Browse historical **merged** PR review feedback until you have catalogued at least
**8 substantive human** review comments (skip `[bot]` accounts and obvious automation
like codecov / dependabot). Useful commands:

```
GH_TOKEN=dummy gh pr list --repo <owner>/<repo> --state merged --limit 30
GH_TOKEN=dummy gh api repos/<owner>/<repo>/pulls/<PR_NUMBER>/reviews
GH_TOKEN=dummy gh api repos/<owner>/<repo>/pulls/<PR_NUMBER>/comments
GH_TOKEN=dummy gh api repos/<owner>/<repo>/issues/<PR_NUMBER>/comments
```

If the first batch is sparse, raise `--limit` or walk older PR numbers. The user
message may include **preloaded samples** — verify and extend them with `gh`, don't
just trust them.

Identify the top ~5 human reviewers by volume and note their phrasing, what severity
they assign, and what they routinely ignore.

## 2. Extract concrete, repo-specific patterns

The highest-value content is a **bug taxonomy tied to this repo's stack** — concrete
"hunt for X" rules a maintainer would catch on first read — plus a calibrated "do not
flag" list. Pair each pattern with the failure mode and, where you saw it, the kind of
diff that triggered it. Avoid generic advice that would apply to any repo.

Cover:
- What the team routinely flags vs. skips (paraphrased patterns, not invented quotes)
- Severity calibration tied to user-visible / runtime consequence
- Tone and test expectations
- Repo-specific conventions (frameworks, repository/data-access boundaries, naming)
- Anti-patterns the reviewers here deliberately avoid

Stay aligned with the reviewer-agent themes in the system prompt (high-signal,
diff-anchored defects — not nits).

## 3. Save

Only after real research, call `save_review_style_prompt` once with:
- `custom_prompt`: 400–1200 words teaching the reviewer this repo's norms.
- `analysis_summary`: 2–4 sentences for the dashboard.
- `top_reviewers` (comma-separated logins), `prs_sampled`, `reviews_sampled`.

Do **not** save a generic guide after one or two commands. Only after ~25+ merged PRs
with zero human feedback may you save a short, conservative guide — and say so in
`analysis_summary`.
