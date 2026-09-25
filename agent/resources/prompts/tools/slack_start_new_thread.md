Start a Slack thread with a headline root and instructions as the first reply.

When called from a Slack thread, post nothing in that thread before this call. On success the tool marks the request with a reaction itself, so end the turn with `slack_no_reply_needed`; on failure, tell the asker why. Outside a Slack thread, share the returned `slack_url` and `dashboard_url` with the asker.
