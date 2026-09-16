Read the most recent top-level messages in a Slack channel.

Use this to see what a channel has been talking about — the conversation
around a request that names something you cannot otherwise see ("this
error", "that PR", "the thing we discussed"). Provide the channel_id and
optionally how many messages to read (default 30, newest last).

Channel history does not contain thread replies. A message with replies is
marked `[thread: N replies, thread_ts=...]`; pass that thread_ts to
`slack_read_thread_messages` to read the thread itself.
