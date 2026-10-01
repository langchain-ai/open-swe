Start an independent Slack thread for separate work, with a headline root and instructions as the first reply. The current conversation remains active; to break out the current conversation and stop listening to the original thread, use `slack_move_thread` instead.

When called from a Slack thread, post nothing in that thread before this call. On success the tool marks the request with a reaction itself, so end the turn with `slack_no_reply_needed`; on failure, tell the asker why. Outside a Slack thread, share the returned `slack_url` and `dashboard_url` with the asker.
