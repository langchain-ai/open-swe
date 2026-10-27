Break out a separate task using the same feature as `/breakout`, with a headline root and self-contained instructions as the first reply.
Pass a requested model ID through `model` and reasoning effort through `effort`, not only in `instructions`.
A model without effort uses its default; effort alone applies to the parent's current model; omitting both preserves inheritance.
Invalid selections return `available_models` with valid IDs and efforts without creating a thread.
This is only for requested breakouts, not a general mechanism for creating Slack threads.
Same-channel breakouts also work in private channels.
Changing channels requires a public source channel and destination.
Omit `channel_id` to use the current channel.
Pass `channel_id` only when the user explicitly chooses a destination.
The current conversation remains active; to move the current conversation and stop listening to the original thread, use `slack_move_thread` instead.

Consider the audience before creating a Slack breakout.
Without an explicit destination, the tool checks the channel's member count in one Slack channel lookup and pauses before posting at 50+ members or when the count is unavailable.
If it asks for a destination, ask the requester whether to keep the breakout here or use a quieter, relevant channel.
Use `slack_list_channels` to find suitable destinations rather than guessing, and offer concrete choices with `slack_reply` options when available.
Once the requester chooses, pass that channel's ID explicitly, including the current channel if they choose to stay.
Never silently change channels, create a code channel, or choose a web breakout to avoid a crowded channel.
Preserve private-channel boundaries: do not suggest moving private source content to a public channel.
Explicit destinations and web-only breakouts bypass the audience check.

Choose the breakout's model deliberately rather than automatically inheriting the parent's.
Unless the user specified a model, actively consider a more powerful available model for complex debugging, architectural decisions, broad or ambiguous implementation work, or tasks requiring deep reasoning across multiple systems.
Prefer the stronger model when the added reasoning is likely to improve the outcome; keep the inherited model for straightforward, narrowly scoped work.
Respect explicit model and effort choices, use only known supported model IDs and efforts, and pass your selection in `model` and `effort` rather than only describing it in `instructions`.
This selects the new thread's model; it does not switch the current conversation's model.

Set `web_only=true` only when the user explicitly asks for a web breakout (the equivalent of `/breakout:web`).
Never choose a web breakout proactively or merely because a question seems quick.
It creates a private, web-only conversation seeded with the full Slack transcript, with no Slack attachment or destination; omit `channel_id`.
Share the returned `dashboard_url` with the requester.
The Slack completion instructions below apply only when `web_only=false`.

When called from a Slack thread, post nothing in that thread before this call except a necessary destination question under the audience guidance above.
On success the tool marks the request with a reaction itself, so end the turn with `slack_no_reply_needed`; on failure, tell the asker why.
From a Slack slash-command run, share the returned `slack_url` and `dashboard_url` with the asker.
