Read messages from a Slack thread.

Use this tool to read messages from a Slack channel or thread.
Provide the channel_id and message_ts (thread timestamp) to fetch all messages in that thread.

If you encounter a Slack message URL like https://workspace.slack.com/archives/C0AME1J0/p1776281321762829 you can extract the channel_id (C0AME1J0) and convert the timestamp by inserting a dot 6 digits from the end (1776281321.762829).

Outside a run started from Slack, this follows the same rules as `slack_read_channel_messages`: available in private assistant threads, public channels only, reading with the workspace bot's own access rather than the asker's, so a private channel, a DM, or a channel shared with another organization is refused.

Returns formatted thread messages with author names and forwarded-message context.
