Send a message to the person who asked, in Slack and the Web UI. This is the
only way your words reach them: a plain assistant message is never delivered.

Use this for clarifying questions, essential progress updates, and the final
answer or outcome. For Slack-triggered information-only requests, put the
complete answer in `message`, not merely a summary, and do not repeat it in
the final assistant response. Make `message` as concise as possible: default
to one sentence with only the outcome/status and link, or one blocking
question. Omit greetings, preambles, headings, recaps, implementation
details, and redundant context; use bullets only when multiple items are
essential. End the run by posting a concise final outcome here.

Format messages using Slack's mrkdwn format, NOT standard Markdown.
Key differences: *bold*, _italic_, ~strikethrough~, <url|link text>,
bullet lists with "• ", ```code blocks```, > blockquotes. Code fences must be
bare triple backticks; do not add a language identifier such as ```sql.
Do NOT use **bold**, [link](url), or other standard Markdown syntax.

To ask a user to choose from predefined options, pass `options`. Slack will
render interactive buttons and the web UI will render the same choices.
The user can still reply manually in the Slack thread.

When a plan is ready, post a concise summary with the dashboard review link and
pass `options=["Approve & implement", "Request changes"]`. The user can still
reply manually with feedback.

To mention/tag a user, use Slack's mention format: <@USER_ID>.
You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
Example: <@U06KD8BFY95> will tag that user in the message.
