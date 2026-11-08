Report these blockers to the requester.
Do not work around this refusal by posting a manual Slack review request, requesting reviewers through GitHub, or using another review tool to bypass the readiness gate, even when the user also asked for a channel post.
If it is a draft, never undraft it yourself.
Give the requester the PR link.
If the review-page link was delivered, say the author received it in DM and ask them to inspect the diff and mark the PR ready on the review page.
Do not offer a Mark ready button in Slack: the author needs to see the diff first.
After marking it ready, the author must ask for human review again; do not promise that this conversation resumes automatically.
If delivery failed, report that failure and ask the author to inspect the diff and use **Ready for review** on GitHub.
Retry request_human_review only after the author has done so; resolve other blockers before retrying.
