Read messages from a Slack thread.

Use this tool to read messages from a Slack channel or thread.
Provide the channel_id and message_ts (thread timestamp) to fetch all messages in that thread.

If you encounter a Slack message URL like https://workspace.slack.com/archives/C0AME1J0/p1776281321762829 you can extract the channel_id (C0AME1J0) and convert the timestamp by inserting a dot 6 digits from the end (1776281321.762829).

Outside a run started from Slack, this follows the same rules as `slack_read_channel_messages`: available in private assistant threads, public channels only, reading with the workspace bot's own access rather than the asker's, so a private channel, a DM, or a channel shared with another organization is refused.

Forwarded message text and a source link do not grant Robin access to the source channel or its original files.
When a read fails because the source channel is inaccessible, explain this limitation clearly to the person, including the reported Slack error, rather than only saying the attachment is missing.
For a confirmed `not_in_channel` error, ask someone with access to invite Robin to the source channel, or to upload the needed file directly in the current conversation.
For `channel_not_found`, explain that the channel could not be found or accessed and ask for a direct upload; do not assume an invitation will fix it.
Outside Slack, an invitation does not bypass the public-channel-only restriction: if that restriction blocks the read, ask for a direct upload instead.
Do not claim the file was deleted or the channel is private unless that was verified.

Returns formatted thread messages with author names and forwarded-message context.
