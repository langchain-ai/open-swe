Post a message to the current Slack thread and the Web UI.

Use this for clarifying questions, essential progress updates, and the final
answer or outcome. For Slack-triggered information-only requests, put the
complete answer in `message`, not merely a summary, and do not repeat it in
the final assistant response. Make `message` as concise as possible: default
to one sentence with only the outcome/status and link, or one blocking
question. Omit greetings, preambles, headings, recaps, implementation
details, and redundant context; use bullets only when multiple items are
essential. End the run by posting a concise final outcome here.

Format `message` using standard Markdown: **bold**, _italic_, ~~strikethrough~~,
[link text](url), and Markdown lists. Replies use Slack's native Markdown blocks.
Use fenced code blocks with a language identifier such as ```python or ```sql
for syntax highlighting, preserving the code's original whitespace.
Messages over 12,000 characters fall back to Slack's legacy mrkdwn and may lose
less-common Markdown formatting. A message with `options` must stay within 12,000
characters so its buttons are not hidden; shorten it or share the body as an artifact.
If supplying explicit `blocks`, use the formatting required by each block type.

To ask a user to choose from predefined options, pass `options`. Slack will
render interactive buttons and the web UI will render the same choices.
The user can still reply manually in the Slack thread.

To mention/tag a user, use Slack's mention format: <@USER_ID>.
You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
Example: <@U06KD8BFY95> will tag that user in the message.
