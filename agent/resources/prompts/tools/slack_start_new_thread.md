Start a Slack thread with a headline root and instructions as the first reply.

When called from a Slack thread, post nothing in that thread before or after this call; the tool marks the request with a reaction itself, so on success end the turn with `slack_no_reply_needed`. Otherwise share the returned `slack_url` and `dashboard_url` with the asker.
