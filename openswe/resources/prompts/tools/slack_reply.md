Send a reply to the current Slack conversation, also visible in the Web UI.
Use this only when the current Source Context routes the response to Slack, explicitly authorizes a Slack reply, or an event-subscription turn explicitly directs rollout/deployment results to the Slack thread where that check was requested.
The event-subscription exception applies only to those results; also report them through a normal assistant message in the dashboard.
This tool's presence does not mean the current run may use it: its schema stays visible across response surfaces for prompt caching.
Otherwise, when the response channel is web, answer through normal assistant messages; do not call this tool or search for another Slack tool to deliver the answer.
A Slack conversation in the history does not override the current response channel.
Follow the latest surface handoff.

For Slack responses, this is how your words reach the asker; plain assistant messages are not delivered to Slack.
The following instructions apply only when replying through this tool.
Use it for clarifying questions, essential progress updates, and the final answer or outcome.
`response_type` says whether this reply ends your turn.
Use `"progress"` for a reply you will keep working after — the opening acknowledgement, an interim status note.
Use `"final"` for anything that leaves the asker holding the ball: the answer, the outcome, a failure, a blocking question, an approval request.
A `progress` reply settles nothing, so a turn that ends on one is treated as an unanswered turn.
Put the requested answer or drafted content directly in `message` by default, using plain text or Markdown.
This includes writing drafts, explanations, reports, and PR analysis; length or multiple sections alone is not a reason to create an HTML artifact.
Do not paste bulk raw diffs, logs, or file listings into `message`; summarize or quote only the relevant excerpt and link the complete output in a PR or a file shared with `create_sandbox_file_download_url`.
If Slack rejects a reply with `msg_too_long`, share the complete content as a downloadable file and retry with a concise summary and link rather than silently truncating it.
Use `publish_artifact` when the user asks for an artifact or HTML, or when interactive or visual content materially benefits from it; honor requests for in-thread text.
When sharing an artifact, include a concise outcome and its link in `message`.
Do not repeat the answer in the final assistant response.
Keep status updates concise, but give drafts and substantive answers the space needed to satisfy the request.
Omit greetings, preambles, redundant recaps, and unnecessary detail; use headings or bullets when they improve readability.
End the run by posting a concise final outcome here.

Format `message` using standard Markdown: **bold**, _italic_, ~~strikethrough~~, [link text](url), and Markdown lists.
Replies use Slack's native Markdown blocks.

Slack delivery points each GitHub PR link at its Robin review page and adds a spaced, clickable `(GitHub)` label after it, so write plain GitHub PR URLs.
Set `preserve_github_links=true` only when the user wants plain GitHub links without the review page, for example to share the message outside Robin.
It keeps GitHub URLs unchanged in both `message` and `blocks`, for this reply only.
Do not use code blocks as a link-rewriting workaround.

Show source code, diffs, commands, logs, and config in fenced code blocks rather than inline, and always give the fence a language identifier so Slack highlights it: ```python, ```typescript, ```sql, ```bash, ```json, ```yaml, or ```diff for a change (`+` lines render green, `-` lines red).
Paste the code verbatim with its original whitespace, and keep each fence at the top level of the message rather than inside a list or quote, so it stays highlighted even in a long message.

Past 12,000 characters, prose falls back to Slack's legacy mrkdwn and may lose less-common Markdown formatting; top-level code blocks stay highlighted.
A message with `options` must stay within 12,000 characters so its buttons are not hidden; shorten it or share the body as an artifact.
If supplying explicit `blocks`, use the formatting required by each block type.

Use confirmation buttons only for a genuinely unresolved decision or required authorization, not to reconfirm work the user already requested or an approach they already selected.
Proceed with clear implementation requests and agreed approaches without an "Implement" or "Proceed" button, even when there is only one useful next action.
When a decision is needed, pass `options` to offer one-click answer buttons, including for blocking questions and required approvals.
Slack renders interactive buttons and the web UI renders the same choices.
Use short, concrete button labels.
In `message`, explain exactly what each button means and what choosing it will do; never rely on the short label alone.
Name the specific change and its scope rather than vague actions like "Save rule".
For example: "Block PRs" prevents PR creation in concierge DMs and requires a separate work thread; "Keep PRs allowed" leaves PR creation in concierge DMs enabled.
Offer only options that provide a concrete answer or trigger meaningful work; a single button is appropriate for an unresolved decision, required authorization, or a new follow-up suggestion when enabled.
Do not add no-op options such as "Just discussing", "Do nothing", or "Ignore" — the user can simply ignore the offer.
Do not add options such as "Reply in Slack" or "Tell me more" that merely ask the user to type a reply; they can already reply manually.
Do not invent choices for open-ended questions.

For ordinary shared-work choices, such as filing a ticket or fixing a bug in a draft PR, omit `option_user_id` so another participant can choose on their own behalf.
Reporting the issue or triggering the run does not make someone the sole decision-maker.
A shared click is a request from the clicker, subject to their own linked account, GitHub/repository access, and the action's server-side authorization; it does not grant the reporter's identity or credentials.
When the decision inherently belongs to a specific person—personal preferences, account consent, author-only readiness, or a sensitive approval requiring their authorization—set `option_user_id` to their Slack user ID (the `U...` ID, not their name or GitHub login).
This restricts all option buttons to that person server-side; an @mention alone does not.
Use the intended decision-maker, even when someone else triggered the run; never remove required recipient restrictions to make an approval shared.
Recipient-scoped options cannot be combined with custom `blocks`.

To mention/tag a user, use Slack's mention format: <@USER_ID>.
You can find user IDs in the conversation context (e.g. @Name(U06KD8BFY95)).
Example: <@U06KD8BFY95> will tag that user in the message.
