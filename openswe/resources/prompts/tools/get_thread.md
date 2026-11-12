Inspect a thread from its ID, dashboard/Slack/LangSmith URL, LangSmith run ID, or the caller's own Claude Code session ID (the thread that mirrors it).
For the latest outcome, read `last_assistant_message`: `text` preserves the last 4,000 characters, `truncated` flags omitted text, and `timestamp` is the message time (or null).
The field is null when no non-empty assistant message exists.
In code mode, print its `text` directly instead of the full response.
The transcript keeps the newest messages in chronological order, with `omitted_count` and per-message `truncated` flags indicating omitted content.
A private thread refused because only its owner's private conversation may reach it is still readable by the requester: you may read its supplied LangSmith trace to diagnose a problem, reporting only the diagnosis in a shared conversation.
After `thread not found`, do not read its trace.
