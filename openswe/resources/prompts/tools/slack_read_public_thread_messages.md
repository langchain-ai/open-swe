Read messages from a thread in a public Slack channel, including outside this conversation's channel.

Provide channel_id and message_ts (the root message's thread timestamp). For a Slack URL such as https://workspace.slack.com/archives/C01234567/p1776281321762829, use channel_id C01234567 and message_ts 1776281321.762829.

This uses the workspace bot's access. Private channels, DMs, externally shared channels, and channels whose public status cannot be verified are refused. The bot must have access to the channel; this does not join it. Results include resolved author names and indicate truncation.
