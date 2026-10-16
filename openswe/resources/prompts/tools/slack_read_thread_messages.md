Read messages from a Slack thread.

Use this tool to read messages from a Slack channel or thread.
Provide the channel_id and message_ts (thread timestamp) to fetch all
messages in that thread.

If you encounter a Slack message URL like
https://workspace.slack.com/archives/C0AME1J0/p1776281321762829
you can extract the channel_id (C0AME1J0) and convert the timestamp
by inserting a dot 6 digits from the end (1776281321.762829).

Returns formatted thread messages with author names and forwarded-message context.
