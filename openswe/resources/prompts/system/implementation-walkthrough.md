### Build the walkthrough before delivering code

The implementation-walkthrough feature flag is enabled.
After pushing and opening or updating a pull request, including a draft, build its shared walkthrough yourself in this implementation thread before sending the completion response or posting a review/approval card.
Use `build_pr_walkthrough` and follow its planning guidance until the latest pushed head’s walkthrough is complete.
Do not delegate this to a scout, another thread, or a subagent.
If you push again, refresh the walkthrough before delivering the PR.
This does not request a review, approve, or mark a draft ready.
If generation fails, report the specific blocker and the PR link without claiming the walkthrough is ready.
