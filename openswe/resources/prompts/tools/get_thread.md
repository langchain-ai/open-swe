Inspect a thread from its ID, dashboard/Slack/LangSmith URL, or LangSmith run ID.
For the latest outcome, read `last_assistant_message`: `text` preserves the last 4,000 characters, `truncated` flags omitted text, and `timestamp` is the message time (or null).
The field is null when no non-empty assistant message exists.
In code mode, print its `text` directly instead of the full response.
The transcript keeps the newest messages in chronological order, with `omitted_count` and per-message `truncated` flags indicating omitted content.
