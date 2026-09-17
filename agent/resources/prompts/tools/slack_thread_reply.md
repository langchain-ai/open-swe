Post a message to the current Slack thread and the Web UI.

Use this for clarifying questions, essential progress updates, and the final
answer or outcome. For Slack-triggered information-only requests, put the
complete answer in `message`, not merely a summary, and do not repeat it in
the final assistant response. Make `message` as concise as possible: default
to one sentence with only the outcome/status and link, or one blocking
question. Omit greetings, preambles, headings, recaps, implementation
details, and redundant context; use bullets only when multiple items are
essential. End the run by posting a concise final outcome here.

Set `should_ask_for_feedback=True` only when this message completely answers an
information-only request, with no clarification or further work needed.
This offers the requester a private rating after the run succeeds. Leave it
False for progress, plans, approval requests, blockers, partial answers, and
coding/PR outcomes; coding tasks request feedback when their PR merges.

Format messages using Slack's mrkdwn format, NOT standard Markdown.
Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
bullet lists with "• ", ```code blocks```, > blockquotes. Code fences must be
bare triple backticks; do not add a language identifier such as ```sql.
Do NOT use **bold**, [link](url), or other standard Markdown syntax.

To ask a user to choose from predefined options, pass `options`. Slack will
render interactive buttons and the web UI will render the same choices.
The user can still reply manually in the Slack thread.

For anything `options` cannot express, pass `blocks` with Block Kit JSON:
`section` `fields` for name/value pairs, a `divider` between findings, an
`actions` block of buttons, a `static_select`, a `datepicker`. Interactive
elements work — a click comes back to this thread and tells you which
`action_id` was used and what was chosen, so name each one for what it means
(`rerun_tests`, `pick_base_branch`) and you will read that name back. A button
carrying `url` is a plain link and never comes back. An `input` block is
refused: a text input is only submitted through a modal, so ask for text in the
message instead. A button and an overflow are spent once used; a select or a
picker can be changed as often as the person likes. Keep `message` as the full
plain-text fallback — it is what notifications, screen readers, and the web UI
show — and skip blocks entirely when a sentence would do.

When a plan is ready, post a concise summary with the dashboard review link and
pass `options=["Approve & implement", "Request changes"]`. The user can still
reply manually with feedback.

To mention/tag a user, use Slack's mention format: <@USER_ID>.
You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
Example: <@U06KD8BFY95> will tag that user in the message.
