Merge a pull request using the approvals collected on its expedited review card in Slack. Call it once checks are green and reviews are clean, typically when a `/baby-sit` watch reports the PR ready, or when you are told the card reached two approvals.

It checks the pull request again before doing anything. If the diff the card showed is unchanged, it submits each approval as that person's GitHub review on the current head and merges. A commit that touched only files the card did not draw, such as tests, keeps the votes. Anything else it returns without merging:

- `needs_approvals`: wait. You are woken when the card has enough.
- `not_ready`: fix the listed blockers. If you only need to wait, keep the `/baby-sit` watch running.
- `invalidated`: the diff voters saw changed, so their votes were discarded. Call `expedite_pr_approval` again for a fresh card.
- `refused`: GitHub's branch protection said no. Report it and ask a maintainer. Never work around it.
