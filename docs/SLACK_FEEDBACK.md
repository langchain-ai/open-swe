# Slack thread feedback

After a successful Slack run, Open SWE sends the person who requested it a private
prompt in the same Slack thread. They can choose **Very Bad**, **Bad**, **Okay**,
**Good**, or **Great**, then use **Add comments** in the acknowledgment to open an
optional comment form. Selecting another rating updates their feedback. Comments
are limited to 3,000 characters. Feedback does not start another agent run.

The prompt uses the completed run's response mapping, so feedback on an older
response remains associated with that run and its requester. Duplicate completion
callbacks and older rating retries do not replace newer feedback. Runs without a
mapped response or requester, interrupted/failed runs, reviews, investigations, and
automated wakeups do not prompt.

Ratings and comments are saved in the LangGraph Store under
`("slack_thread_feedback", channel_id)`, keyed by run ID. They are also exported as
LangSmith **thread feedback**, with a deterministic key
`slack_rating:{channel_id}:{user_id}:{run_id}`. Scores range from 0 (Very Bad) to 1
(Great), in increments of 0.25. A failed LangSmith export is logged; the Store record
remains saved, and another rating or comment submission attempts export again.

This uses the existing authenticated run-completion webhook and Slack interactivity
endpoint (`/webhooks/slack/interactivity`). Slack interactivity and completion
callbacks must already be enabled for the deployment. No new scopes or dependencies
are required. Prompts and acknowledgments are Slack ephemeral messages: they are
visible only to the requester and do not persist across Slack sessions. Stored
feedback is available to the Open SWE/LangSmith workspace, not posted to the channel.
