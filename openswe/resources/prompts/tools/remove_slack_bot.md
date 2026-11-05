Remove one bot from the Slack bot allowlist, revoking its permission to trigger Open SWE.
Use only when an admin explicitly asks to remove that bot.
Pass the team_id and bot_id from list_slack_bots; removing an absent entry is harmless.
Requires a currently authorized admin in a private admin surface or verified sole-writer shared thread.
Shared threads receive only an acknowledgement.
Authorization is rechecked on every call.
The mutation is audited with the Slack workspace and bot ID.
