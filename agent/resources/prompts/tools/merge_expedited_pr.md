Merge a pull request using the approval collected on its expedited review card in Slack. Call it once checks are green and reviews are clean, typically when a `/baby-sit` watch reports the PR ready, or when you are told someone approved the card.

It checks the pull request again before doing anything. If the diff the card showed is unchanged, it submits the approval as that person's GitHub review on the current head, comments the card's link on the PR, and merges. A commit that touched only files the card did not draw, such as tests, keeps the approval. Anything else it returns without merging:

- `needs_approvals`: wait. The author may still have to mark the draft ready, or nobody has approved yet; you are woken when someone does.
- `not_ready`: fix the listed blockers. If you only need to wait, keep the `/baby-sit` watch running.
- `invalidated`: the diff the approver saw changed, so the approval was discarded. Call `expedite_pr_approval` again for a fresh card.
- `refused`: GitHub's branch protection said no. Report it and ask a maintainer. Never work around it.
