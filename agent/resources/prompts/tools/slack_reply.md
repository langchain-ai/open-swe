Send a message to the person who asked, in Slack and the Web UI. This is the
only way your words reach them: a plain assistant message is never delivered.

Use this for clarifying questions, essential progress updates, and the final
answer or outcome. `response_type` says whether this reply ends your turn.
Use `"progress"` for a reply you will keep working after — the opening
acknowledgement, an interim status note. Use `"final"` for anything that leaves
the asker holding the ball: the answer, the outcome, a failure, a blocking
question, an approval request. A `progress` reply settles nothing, so a turn
that ends on one is treated as an unanswered turn.
For Slack-triggered information-only requests, put the
complete answer in `message`, not merely a summary, and do not repeat it in
the final assistant response. Make `message` as concise as possible: default
to one sentence with only the outcome/status and link, or one blocking
question. Omit greetings, preambles, headings, recaps, implementation
details, and redundant context; use bullets only when multiple items are
essential. End the run by posting a concise final outcome here.

Format `message` using standard Markdown. Slack renders it natively and the web
UI shows the same text:

- **bold**, _italic_, ~~strikethrough~~, `inline code`, [link text](url)
- bulleted and numbered lists, and task lists (`- [ ] todo`, `- [x] done`)
- `> quotes`, `---` dividers, and `###` headings when structure helps
- pipe tables (`| a | b |`) for short comparisons
- fenced code blocks with a language identifier such as ```python, ```sql, or
  ```diff for syntax highlighting, preserving the code's original whitespace

Images are not shown inline; Slack turns them into links. Past 12,000
characters, prose falls back to Slack's legacy mrkdwn and loses tables, task
lists and less-common formatting; top-level code blocks stay highlighted. A
message with `options` must stay within 12,000 characters so its buttons are
not hidden; shorten it or share the body as an artifact.

`blocks` adds Slack Block Kit blocks after `message`, for what Markdown cannot
show. They render only in Slack: the web UI and notifications show `message`
alone, so it must still carry the answer. Use display blocks only; never add
buttons, menus, inputs or other interactive elements, and use `options` for
choices. `message`, `blocks` and any `options` together get at most 50 blocks.

- A chart, at most two per message. `chart.type` is `bar`, `line`, `area`, or
  `pie`; titles stay within 50 characters, labels and series names within 20,
  with up to 12 series of up to 20 points each (a pie takes up to 12
  `segments` of `{"label", "value"}` instead of `series`):
  `{"type": "data_visualization", "title": "p95 latency (ms)", "chart": {"type": "line", "series": [{"name": "api", "data": [{"label": "Mon", "value": 120}, {"label": "Tue", "value": 95}]}], "axis_config": {"categories": ["Mon", "Tue"], "x_label": "Day", "y_label": "ms"}}}`
- A table needing right-aligned numbers or wrapped cells: up to 100 rows of up to
  20 cells and 10,000 characters, the first row being the header:
  `{"type": "table", "column_settings": [{"is_wrapped": true}, {"align": "right"}], "rows": [[{"type": "raw_text", "text": "Service"}, {"type": "raw_text", "text": "Errors"}], [{"type": "raw_text", "text": "api"}, {"type": "raw_number", "text": "42"}]]}`
- A plan whose steps carry a status of `pending`, `in_progress`, or `complete`,
  up to 50 tasks with unique `task_id`s:
  `{"type": "plan", "title": "Rollout", "tasks": [{"task_id": "1", "title": "Migrate schema", "status": "complete"}, {"task_id": "2", "title": "Backfill", "status": "in_progress"}]}`

To ask a user to choose from predefined options, pass `options`. Slack will
render interactive buttons and the web UI will render the same choices.
The user can still reply manually in the Slack thread.

To mention/tag a user, use Slack's mention format: <@USER_ID>.
You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
Example: <@U06KD8BFY95> will tag that user in the message.
