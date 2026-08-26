---
name: ci-blocker-scan
description: Survey pull-request CI for systemic failures without treating stale GitHub CLI results as an all-clear.
---

# Scan for systemic CI blockers

Use this skill for scheduled or webhook-triggered CI-blocker surveys across pull requests.

## Establish the probe

1. Run `date -u` first and record the resulting UTC timestamp as the start of this tick. Use it to calculate the investigation window; do not infer the current time from GitHub results.
2. Discover and record the repository's real workflow names before querying them. Use `gh api repos/<owner>/<repo>/actions/workflows --paginate` and preserve each workflow's exact `name` and `path`.
3. Record the `--json` field names supported by the installed GitHub CLI before using them. Check `gh run list --help` and `gh pr view --help`, and use only fields those commands document for the installed version.

## Gather reliable run data

1. Enumerate candidate completed failing pull-request runs with `gh api 'repos/<owner>/<repo>/actions/runs?event=pull_request&per_page=100'`. Paginate or narrow by workflow, status, and date only after collecting the repository's actual workflow names.
2. For affected pull requests, corroborate the check state with `gh pr view <PR> --json statusCheckRollup` using the recorded supported field name.
3. Use `gh run list --workflow ... --event pull_request` only as a secondary view. If its newest `createdAt` predates the investigation window, the page is stale or incomplete and is inconclusive; never use it alone to conclude that recent failures do not exist or that all failures are outside the window.
4. If the API and per-PR paths cannot corroborate the absence or age of failures during this tick, report an inconclusive probe. Do not emit an all-clear.

## Verdict rules

- An “all failures outside the window” verdict requires corroboration from the actions-runs API or per-PR `statusCheckRollup` path in this same tick.
- A “no systemic CI blocker” verdict requires the same corroboration, including a check of candidate pull-request runs that the secondary `gh run list` page may omit.
- When corroboration is unavailable, say that the CI probe is inconclusive and identify which reliable path could not be completed. Continue investigation on the next tick rather than suppressing analysis, alerting, or remediation based on an all-clear.

Keep the recorded workflow names, workflow paths, and supported CLI JSON fields with the tick's evidence so later analysis does not guess renamed workflows or unsupported fields.
