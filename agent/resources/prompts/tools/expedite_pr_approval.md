Ask for a tiny pull request to be approved and merged from a Slack card instead of a normal GitHub review. Any open pull request qualifies, not only ones you opened.

Use it only for a change of at most 10 lines in ordinary text files that a reviewer can judge from the diff alone. Never for auth, secrets, CI workflows, dependency manifests, migrations, or binaries; those are refused. Call it once after pushing; do not poll. Open SWE watches the PR, posts the diff with Approve and Reject buttons once every check is green and every review is clean, and merges after two distinct approvals. You are told if it is rejected or withdrawn. Use `action="cancel"` to withdraw a request you no longer want.

The card goes in the current Slack thread when there is one. Pass `channel` (a Slack channel name like `#eng-reviews` or a channel id) to post it as a new thread in that channel instead; this is required when the conversation is not in Slack.
