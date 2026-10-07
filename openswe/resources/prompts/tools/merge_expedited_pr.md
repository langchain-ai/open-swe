Merge a pull request using the approval collected on its expedited review card in Slack. Call it once checks are green and reviews are clean, typically when a `/baby-sit` watch reports the PR ready, or when you are told someone approved the card.

It checks the pull request again before doing anything. If the diff the card showed is unchanged, it submits the approval as that person's GitHub review on the current head, comments the card's link on the PR, and merges. A commit that touched only test files keeps the approval. Anything else it returns without merging:

- `needs_approvals`: wait. The author may still have to mark the draft ready, or nobody has approved yet; you are woken when someone does.
- `not_ready`: fix the listed blockers. If you only need to wait, keep the `/baby-sit` watch running.
- `diff_changed`: a commit changed non-test lines the approver saw. Nothing was discarded; decide whether the change needs the approver to look again. When it does not (a mechanical fix such as a rename, import, or lint correction that leaves the approved behavior as it was), call this tool again with `keep_approval_reason`, one sentence naming what changed and why it needs no re-review; it is posted on the PR. When it does (new behavior, a different approach, or anything the approver would want to see), call `expedite_pr_approval` for a fresh card.
- `invalidated`: the diff grew past what expedited review allows, so the approval was discarded. Ask for a normal GitHub review.
- `refused`: GitHub's branch protection said no. Report it and ask a maintainer. Never work around it.
