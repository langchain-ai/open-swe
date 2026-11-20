Search messages in the workspace's public Slack channels, for research into past discussions, decisions, incidents, or who worked on something.

Phrase `query` as a natural-language question to get semantic results, or use keywords; Slack search modifiers such as `in:<#C123>`, `from:<@U123>`, `before:2026-01-31`, `after:2026-01-01`, and `has:pin` work inside `query`.
`sort` is `score` (relevance, the default) or `timestamp` (newest first, keyword matching only).
Each call returns up to `limit` messages (at most 20) with their channel, author, text, timestamp and permalink, plus `next_cursor` to pass as `cursor` for the next page; an empty `next_cursor` means there are no more.
Read a hit's thread with `slack_read_thread_messages`, passing its `channel_id` and `message_ts`.

One message allows six searches, shared with every subagent working on it and counting each page, so start with one well-phrased query and page or reword once at most.
Cite the permalinks of the messages you rely on.

Slack's bot search API requires a per-message grant, normally supplied with an @mention or DM, and this tool refuses channels shared with another organization.
Do not infer whether a grant exists from `explicit_bot_mention` alone; try the tool when search is useful.
If the grant is missing or expired, continue with `slack_read_channel_messages` in relevant accessible channels and `slack_read_thread_messages` for linked or candidate threads.
`slack_search_channel_directory` can locate candidate public channels without a grant.
Each tool enforces its own access rules.
Do not stop the investigation or ask the person to repeat their request with an @mention just to unlock search, including in listening channels and untagged follow-ups.
Use other available sources too, and ask only for concrete missing information that still prevents the answer after those fallbacks.
Reading specific channels and threads is not a workspace-wide search.
When you answer from those reads, name the channels and threads you read, say you could not search all of Slack, and do not call those reads a search.
A missing local grant is not a search request refused by Slack.
Private channels, DMs, and group DMs are never searched.
