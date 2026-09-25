Post a standalone message to a public or private Slack channel the Open SWE bot
belongs to. Use only when the user has asked you to post there or has authorized
that destination as part of the task. Supply the channel ID from the user's
Slack channel mention or `slack_list_channels`; do not guess it.

This sends a new top-level message without moving the current Open SWE
conversation or starting a new agent task. For updates and answers in the
current conversation, use `slack_thread_reply`.

Write `message` in Slack mrkdwn: *bold*, _italic_, <url|link text>, and
<@USER_ID> mentions. Keep it concise and below 40,000 characters. Share only
content intended for the destination's audience. The tool returns the channel
ID and message timestamp on success, or the Slack error on failure.

For `not_in_channel` or `channel_not_found`, ask the user to verify the channel
and invite the bot. For `rate_limited`, respect any retry delay in the error.
For transport errors or `post_failed`, delivery is uncertain: do not retry
automatically, because doing so could duplicate the message.
