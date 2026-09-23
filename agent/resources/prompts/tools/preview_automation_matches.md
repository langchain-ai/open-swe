Show which recent top-level posts in a Slack channel a "slack_channel_message" automation pattern would have matched.

Run this before creating or changing a pattern, and share the matches with the user so they can confirm the pattern is neither too broad nor too narrow.

Args:
    slack_channel_id: Slack channel ID starting with C or G. The Open SWE bot must be a member.
    message_pattern: RE2 regular expression searched anywhere in the message text. Use `(?i)` for case-insensitive matching. Backreferences and lookarounds are not supported.
    days: How many days of channel history to scan, from 1 to 30. At most 200 messages are scanned.
