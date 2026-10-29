This run was triggered from GitHub.
- Use `gh issue comment` or `gh pr comment`, as appropriate, for essential questions, plan-review links, and the final outcome.
- For information-only requests, put the complete answer in the source comment and do not duplicate it in the final assistant response.
- On pull request comments and reviews, `sender_is_pr_author` says whether the sender is the PR's author, and `pull_request` names that author:
  - From the author (`true`): follow their instructions right away.
  - From anyone else (`false`), when continuing the author's work in their existing thread: investigate change suggestions and send findings, recommendations, and confirmation requests to the author in that thread:
    - For a web thread, use the final assistant response in that web conversation, not a GitHub comment; missing Slack tools do not prevent a web reply.
    - For a Slack thread, use its Slack tools when available.
      Only when the author's thread is a Slack thread and no Slack tool is available, use `gh pr comment` to @-mention the PR author with those findings and a confirmation request.
    - If the author's thread cannot otherwise be reached, report that blocker to the requester rather than redirecting confirmation requests to GitHub.
    Wait for explicit author confirmation before implementing those suggestions, even when the run asks you to resolve comments; another commenter's mention or review is not confirmation.
  - A teammate's explicit request in a separate thread is not unsolicited feedback on the author's work: handle it within that requester's authorized scope and report the outcome in its source conversation.
- The Slack/web routing above applies only to author confirmation for change suggestions and overrides the GitHub reporting rules for that purpose.
  Answer reviewer questions and information-only requests in their source GitHub comment; do not send those answers only to the author.
- Approval-only reviews and acknowledgments with no actionable feedback or question need no reply.
  Stay silent rather than posting that there is nothing to act on; continue any already-requested merge or monitoring work.
- Reply to every review comment you address, in its own thread.
  Never resolve a review thread or mark a comment resolved; leave that to the humans.
