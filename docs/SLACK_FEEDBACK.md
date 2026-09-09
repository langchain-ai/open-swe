# Slack thread feedback

Open SWE sends a private feedback prompt after either a tracked PR is merged or
an information-only question is answered successfully. For questions, the agent
marks its complete Slack reply with `question_answered=True`; the completion
webhook prompts only after that run succeeds. The flag defaults to false and is
ignored for replies with choice buttons. Progress updates, clarifying questions,
plans, approvals, PR creation, and PR closure without merging do not prompt.

The requester sees the prompt in the same Slack thread. They can choose
**Very Bad**, **Bad**, **Okay**, **Good**, or **Great**, or use **Add comments** directly
in that prompt to open a text form. Text feedback does not require a rating.
Selecting a rating later adds it to the same feedback and preserves the comment.
Comments are limited to 3,000 characters. Feedback does not start another agent run.

The prompt uses the completed run's response mapping, so feedback on an older
response remains associated with that run and its requester. PR records preserve
the originating Slack channel and run when opened; GitHub's merge webhook uses
that association even after later turns. Older PRs without that association do
not prompt. Duplicate merge/completion callbacks and older rating retries do not
replace newer feedback. Runs without a mapped response or requester and reviewer
threads do not prompt. Answer prompts also exclude failed/interrupted runs and
automated wakeups.

Ratings and comments are saved in the LangGraph Store under
`("slack_thread_feedback", channel_id)`, keyed by run ID. They are also exported as
LangSmith **thread feedback**, with a deterministic key
`slack_rating:{channel_id}:{user_id}:{run_id}`. Scores range from 0 (Very Bad) to 1
(Great), in increments of 0.25; text-only feedback has no score. A failed LangSmith
export is logged; the Store record
remains saved, and another rating or comment submission attempts export again.

This uses the existing authenticated run-completion and GitHub webhooks, plus the
Slack interactivity endpoint (`/webhooks/slack/interactivity`). Slack interactivity,
completion callbacks, and GitHub `pull_request` events must be enabled for the
deployment. No new scopes or dependencies are required. Prompts and acknowledgments
are Slack ephemeral messages: they are visible only to the requester and do not
persist across Slack sessions. Stored feedback is available to the
Open SWE/LangSmith workspace, not posted to the channel.
