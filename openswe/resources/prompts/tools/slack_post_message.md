Post a standalone message to a public or private Slack channel the Open SWE bot
belongs to. Avoid using this tool in general: prefer replying in the current
conversation with `slack_reply`. Use it only when the user explicitly asks for a
standalone post to that destination, not for unsolicited announcements or updates.
Supply the channel ID from the user's Slack channel mention or
`slack_list_channels`; do not guess it.

Every post must tag the user who triggered it with their <@USER_ID> mention.
Use their trusted Slack identity from the conversation context; never guess it
or substitute another user. If their Slack user ID is unavailable, ask for it
before posting.

This sends a new top-level message without moving the current Open SWE
conversation or starting a new agent task. For updates and answers in the
current conversation, use `slack_reply`.

Write `message` in standard Markdown: **bold**, _italic_, [link text](url), and
<@USER_ID> mentions. Put source code, diffs, and commands in top-level fenced
code blocks with a language identifier such as ```python or ```diff so Slack
highlights them. Keep it concise and below 40,000 characters. Share only
content intended for the destination's audience. The tool returns the channel
ID and message timestamp on success, or the Slack error on failure.

For `not_in_channel` or `channel_not_found`, ask the user to verify the channel
and invite the bot. For `rate_limited`, respect any retry delay in the error.
For transport errors or `post_failed`, delivery is uncertain: do not retry
automatically, because doing so could duplicate the message.
