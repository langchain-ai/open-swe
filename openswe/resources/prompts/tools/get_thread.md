Inspect a thread from its ID, dashboard/Slack/LangSmith URL, or LangSmith run ID.

`transcript` holds the thread's user and assistant messages, numbered by `index`; tool calls, tool results and system context are never returned (`hidden_count`). The default response is bounded: it pages from the first message, cuts each message short, and always adds the latest assistant message with more room, since it is usually the result you want. Nothing is cut silently:
- A message cut short has `truncated: true`, `remaining_chars` and `continue_with`, the exact `get_thread` call that reads on from where it stopped. Pass `message_id` (with `char_offset` to continue) to read one message in full, page by page.
- When messages are left out, `transcript.next_page` is the call that returns the next page. Pass `message_offset` (negative counts from the end, so `-5` reads the last five) and `message_limit` (1-100) to page through the transcript.

A call with `message_offset` or `message_id` returns only that part of the transcript, not the rest of the thread's details.
