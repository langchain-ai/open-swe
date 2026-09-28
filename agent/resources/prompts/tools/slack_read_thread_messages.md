Read exactly one Slack thread's replies.

Provide the channel_id and a raw message_ts already observed from a Slack permalink,
a thread_ts=... marker in slack_read_channel_messages output, or the timestamp portion
of an evidence ID such as slack:<ts>. Do not pass the slack: prefix.

Never construct message_ts by converting a date, incident created_at, or wall-clock time
to epoch seconds. Values ending in .000000 are invalid for this tool.

If you encounter a Slack message URL like
https://workspace.slack.com/archives/C0AME1J0/p1776281321762829
you can extract the channel_id (C0AME1J0) and convert the timestamp
by inserting a dot 6 digits from the end (1776281321.762829).

Returns formatted thread messages with author names and forwarded-message context.
