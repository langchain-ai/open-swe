Move or break out the current conversation into a new Slack thread, preserving its history and detaching the original thread so Open SWE stops listening there.
Omit `channel_id` to use the current channel.
Pass `channel_id` only when the user explicitly chooses a destination.
Without an explicit destination, the tool pauses at 50+ channel members or an unknown audience size; ask the user to choose, then pass their chosen channel ID explicitly, including the current channel if they want to stay.
Use a concise, non-sensitive `message` for the new root; subsequent replies go to the destination.
Use this to move the current conversation, not `slack_breakout_thread`, which is the `/breakout` feature for separating a task while leaving the original thread active.
