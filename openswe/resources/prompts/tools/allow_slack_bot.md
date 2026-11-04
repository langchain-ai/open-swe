Allow one active bot in the configured Slack workspace to trigger Open SWE.
Use only when an admin explicitly asks to allow that bot.
Pass its Slack bot ID (B…) or bot member ID (U…); Slack verifies its current identity before adding it.
Open SWE cannot allow itself, deleted bots, humans, or bots from another Slack workspace.
Requires a currently authorized admin in a private admin surface or verified sole-writer shared thread.
Shared threads receive only an acknowledgement.
Authorization is rechecked on every call.
The mutation is audited with the resolved Slack workspace and bot ID.
