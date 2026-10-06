List one page of Slack channel members, including their Slack IDs, display names,
and linked GitHub logins when known to Open SWE. Unknown GitHub logins are null;
never infer them from names. Names fall back to Slack IDs if profile lookup fails.
No email addresses or private profile settings are returned.

Supply a channel ID from the conversation context, a Slack channel mention, or
`slack_list_channels`; never guess it. Only public, internally shared channels
or this thread's own channel may be listed. DMs are not supported.

Pass each nonempty `next_cursor` as `cursor` to continue, including after an
empty page. Stop when `next_cursor` is empty; report a repeated cursor or Slack
error rather than treating a partial list as complete.
