Move or break out the current conversation into a new Slack thread, preserving its history and detaching the original thread so Open SWE stops listening there.
Choose the destination from the user's request and applicable channel descriptions and workspace instructions.
Use a concise, non-sensitive `message` for the new root; subsequent replies go to the destination.
Use this to move the current conversation, not `slack_breakout_thread`, which is the `/breakout` feature for separating a task while leaving the original thread active.
