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

For anything richer, pass `blocks` with Block Kit JSON. Reach for it when the
answer has shape a paragraph loses: a `section` with `fields` for a set of
name/value pairs, a `divider` between findings, a `context` line for a caveat,
or an `actions` block of link buttons straight to a PR, a trace, a saved plan,
or a dashboard page. Keep `message` as the full plain-text fallback — it is
what notifications and screen readers show, and it is what the web UI renders.
`blocks` replaces the buttons `options` would have drawn, so pass one or the
other. Blocks are checked against Slack's schema before posting and a bad one
comes back named, so fix what the error points at rather than dropping the
whole reply. Do not reach for Block Kit when a sentence would do; a wall of
blocks for a one-line answer is worse than the sentence.

When a plan is ready, post a concise summary with the dashboard review link and
pass `options=["Approve & implement", "Request changes"]`. The user can still
reply manually with feedback.

To mention/tag a user, use Slack's mention format: <@USER_ID>.
You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
Example: <@U06KD8BFY95> will tag that user in the message.
