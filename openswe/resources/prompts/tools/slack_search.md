Search messages in the workspace's public Slack channels, for research into past discussions, decisions, incidents, or who worked on something.

Phrase `query` as a natural-language question to get semantic results, or use keywords; Slack search modifiers such as `in:<#C123>`, `from:<@U123>`, `before:2026-01-31`, `after:2026-01-01`, and `has:pin` work inside `query`.
`sort` is `score` (relevance, the default) or `timestamp` (newest first, keyword matching only).
Each call returns up to `limit` messages (at most 20) with their channel, author, text, timestamp and permalink, plus `next_cursor` to pass as `cursor` for the next page; an empty `next_cursor` means there are no more.
Read a hit's thread with `slack_read_thread_messages`, passing its `channel_id` and `message_ts`.

One message allows six searches, shared with every subagent working on it and counting each page, so start with one well-phrased query and page or reword once at most.
Cite the permalinks of the messages you rely on.

Slack allows this search only for a run started by a message that @mentions Open SWE or is sent to it in a DM, and only in channels not shared with another organization.
Private channels, DMs, and group DMs are never searched.
