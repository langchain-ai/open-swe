Read the most recent top-level messages in a public Slack channel.

Use this to see what a channel has been talking about — the conversation
around a request that names something you cannot otherwise see ("this
error", "that PR", "the thing we discussed"). Provide the channel_id and
optionally how many messages to read (default 30, newest last).

Two conditions, both refused with an explanation when they do not hold. The
channel must be public: this reads with the workspace bot's own access rather
than the asker's, so a private channel, a DM, or a channel shared with another
organization would leak its membership. And this thread must be private, since
everyone who can read the thread would read whatever it pulls in.

Channel history does not contain thread replies. A message with replies is
marked `[thread: N replies, thread_ts=...]`; pass that thread_ts to
`slack_read_thread_messages` to read the thread itself.
