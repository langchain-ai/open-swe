### Build the walkthrough before delivering code

The implementation-walkthrough feature flag is enabled.
After pushing and opening or updating a pull request, including a draft, build its shared walkthrough yourself in this implementation thread before sending the completion response or posting a review/approval card.
Call `build_pr_walkthrough` with the canonical PR URL to pin the current head and get the unplanned hunks and planning guidance.
Use the same tool to split hunks, place conceptual chunks, and describe Other; keep going until it returns `complete: true` for the latest pushed head.
Do not delegate this to a scout, another thread, or a subagent.
If you push again, refresh the walkthrough before delivering the PR.
This does not request a review, approve, or mark a draft ready.
If generation fails, report the specific blocker and the PR link without claiming the walkthrough is ready.
