Ask for a tiny pull request to be approved and merged from a Slack card instead of a normal GitHub review. Any open pull request qualifies, not only ones you opened.

Use it only for a change of at most 10 lines in text files that a reviewer can judge from the complete diff. Binaries and files without a readable patch are refused; paths are not an eligibility gate. Call it once after pushing; do not poll. Open SWE watches the PR, posts the diff with Approve and Reject buttons once every required check is green and every review is clean, and merges after two distinct approvals. You are told if it is rejected or withdrawn. Use `action="cancel"` to withdraw a request you no longer want.

The card goes in the current Slack thread when there is one. Pass `channel` (a Slack channel name like `#eng-reviews` or a channel id) to post it as a new thread in that channel instead; this is required when the conversation is not in Slack.
