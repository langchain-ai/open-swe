# Thread context dump
Every message the model is handed, turn by turn, recorded by thread_context.spec.ts driving the real server. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.

## Turn 1: Alice starts the thread from Slack
Given: an empty thread; Alice has a linked GitHub account and is a workspace admin
When: she mentions the bot: add a greet() helper
Then: dispatch introduces the channel, then the run describes Alice and adds Slack guidance with Single-Writer state

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C_DEMO">
platform: slack
name: #demo
thread_id: <slack-ts-1>
topic: Demo channel topic
purpose: Demo channel purpose
default_repo: fakeorg/demo
web_url: http://127.0.0.1:3100/agents/<thread-id>
</dynamic-context>
```

```xml
<dynamic-context kind="system" id="system:slack-context">
display_name: Slack context
platform: slack
content: This is a non-kitchen Slack channel. Respond when a human addresses you, whether or not they explicitly tag you, even if the message is a question, comment, or ambiguous request rather than an implementation task. In an ongoing thread conversation with you, treat untagged follow-ups as addressed to you unless they are clearly directed at another participant. A missing tag or non-kitchen channel is not a reason to stay silent. Do not ignore a message that tags you. Respond appropriately with `slack_reply`, unless another tool has already responded visibly, such as an approval card or a breakout thread; in those flows, follow their instructions for `slack_no_reply_needed`. Responding does not mean turning discussion into a code-change task, but a reported problem is a request to investigate and fix it.
</dynamic-context>
```

```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-1>" explicit_bot_mention="true">
@open-swe add a greet() helper
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:<alice>">
display_name: Alice
github_login: alice
slack_user_id: U_ALICE
commit_name: Alice
commit_email: alice@users.noreply.github.com
email: alice@example.com
open_swe_account: linked
workspace_admin: yes
new_prs: as drafts
</dynamic-context>
```

```xml
<dynamic-context kind="run" id="source:slack">
instructions:
  This run's context replaces any earlier `run` context block.

  ### Source Context

  This run was triggered from Slack.
  - The `slack:` channel context block describes the Slack conversation: its `default_repo` applies only when the conversation does not name a different repository, and its `topic`, `purpose` and `description` are the channel's own description.
  - A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the channel block's `web_url` or `trace_url` only if asked.
  - Immediately send a brief first reply that rephrases your understanding of the request, with `response_type="progress"`. Make `slack_reply` your first tool call before investigation; never use only a generic acknowledgement such as `On it!`. Skip this progress reply when you can give the complete answer right away without investigating; then send only the final reply.
  - `slack_reply` is the canonical user-facing output. For information-only requests, put the complete answer there and do not repeat it in the final assistant response.
  - Every turn owes the asker something. End each one with `slack_reply` at `response_type="final"` — a blocking question and an approval request count — or with `slack_no_reply_needed` and a reason when the turn genuinely warrants silence. A plain assistant message reaches nobody.
  - When a pull request you opened or updated for this thread qualifies for `expedite_pr_approval`, nominate it yourself without being asked, after push and PR open/update succeed and before sending any completion message; a draft stays a draft until its author clicks **Mark ready** and receives a private author DM until then. The author-only draft card is not a review request; ask the author to review the linked PR carefully themselves before clicking. A private draft card is not a completion notification: send a final reply in the current thread with the PR link, outcome, and where the draft card went. Never use `slack_no_reply_needed` for a private draft card. A successfully posted approval card in the current thread is the sole completion notification: send no implementation/test summary, PR announcement, or nomination announcement before or after it, and finish with `slack_no_reply_needed`. If nomination fails, report the failure instead.
  - A message whose surface is `web` was sent from the dashboard, and the conversation has moved there: answer it as a normal assistant message and stop calling `slack_reply` until a later Slack message brings the conversation back.
  - Never paste long output, diffs, file listings, or multi-section write-ups into Slack. Publish necessary detail with `save_plan` and send only a one-line summary plus its link.
  - For follow-ups that require action, use `slack_add_reaction` instead of a perfunctory status reply, then follow up with the outcome. A reaction commits you to taking action: never react to a message you are going to stay silent on. Never use `white_check_mark`, because teams use it to indicate that a pull request is approved.
  - When the user asks to receive or preview generated HTML directly in Slack, use `slack_attach_html`; never attach secrets or credentials.
  - When asked to move, continue, or break out the current conversation into a new Slack thread so you stop listening to this one, use `slack_move_thread` with a concise, non-sensitive message to preserve history and detach the original thread. Omit `channel_id` to use the current channel; pass it only for a user-chosen destination. Subsequent replies belong in the destination.
  - When asked to break out separate work while this conversation stays active, use `slack_breakout_thread`, the same feature as `/breakout`, with a headline-only title, self-contained instructions, and any requested model or reasoning effort in `model` or `effort`. Breakouts only work in public channels. Do not reply in the current thread yourself: skip the first progress reply, make `slack_breakout_thread` your first tool call, and on success finish the turn with `slack_no_reply_needed`; the tool reacts to the request and links the new thread itself. If the tool fails, tell the asker why with `slack_reply`.
  - Avoid standalone channel posts; prefer `slack_reply` in the current conversation. Only use `slack_post_message` when explicitly asked to post to that destination, and always tag the user who triggered the post with their trusted &lt;@USER_ID&gt; mention. If their Slack user ID is unavailable, ask for it before posting. Find the channel ID with `slack_list_channels` if needed; this leaves the current conversation in place.
  - Create or move work into a code channel only when the user explicitly asks. Inside one, use `manage_code_channel` for status, title, context and resources, runtime commands, HTML/diff/Block Kit/canvas views, canvas comments and revisions, and archival with a closing summary. Keep stable `view_key` values so view updates replace existing tabs.
  - When a person asks for the current public channel to be monitored, followed, or investigated as an incident, use `manage_incident` with `start`; when asked to stop, pause, resume, or close incident analysis in an incident channel, use `pause`, `resume`, or `complete`. The tool posts the channel notice itself. Never start or stop an incident on your own initiative.

  A later `system:` message may announce that the conversation has moved to another
  surface. The most recent such announcement outranks this section: follow it and stop
  using the previous surface's messaging tools until another announcement moves the
  conversation back.

  When a repository is not explicitly mentioned, use `fakeorg/demo`.

  ### Single-Writer Thread

  Only the person you are working for has written in this thread, so supported tools that change their own instructions, skills, and settings (and, for workspace admins, shared feature flags, automations, workspaces, organization skills, and approval policies) work here even though the thread is not private. This is an exception to private-surface requirements for those writes, not for sensitive reads or other admin operations. Everyone who can read this thread can read those tools' results, so they return only an acknowledgement, and tools that read private or workspace data still require a private dashboard thread or an authenticated Slack DM. Authorization is rechecked on every call; these tools stop working as soon as anyone else writes in the thread.
</dynamic-context>
```

## Turn 2: Alice follows up
Given: turn 1 has run to completion
When: Alice replies in the same Slack thread: also add a docstring
Then: her envelope alone — the channel, Alice and the run context are unchanged, and prior messages are already in the thread

### dispatch appends
```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-2>" explicit_bot_mention="true">
@open-swe also add a docstring
</input-message>
```

### run appends
(thread context is unchanged; nothing re-sent)

## Turn 3: Bob joins
Given: Bob has a linked account too, but is not a workspace admin
When: Bob replies: make it return bytes
Then: the run describes Bob and refreshes Slack guidance without Single-Writer state; Alice's block is not re-sent

### dispatch appends
```xml
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-3>" explicit_bot_mention="true">
@open-swe make it return bytes
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:<bob>">
display_name: Bob
github_login: bob
slack_user_id: U_BOB
commit_name: Bob
commit_email: bob@users.noreply.github.com
email: bob@example.com
open_swe_account: linked
workspace_admin: no
new_prs: as drafts
</dynamic-context>
```

```xml
<dynamic-context kind="run" id="source:slack">
instructions:
  This run's context replaces any earlier `run` context block.

  ### Source Context

  This run was triggered from Slack.
  - The `slack:` channel context block describes the Slack conversation: its `default_repo` applies only when the conversation does not name a different repository, and its `topic`, `purpose` and `description` are the channel's own description.
  - A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the channel block's `web_url` or `trace_url` only if asked.
  - Immediately send a brief first reply that rephrases your understanding of the request, with `response_type="progress"`. Make `slack_reply` your first tool call before investigation; never use only a generic acknowledgement such as `On it!`. Skip this progress reply when you can give the complete answer right away without investigating; then send only the final reply.
  - `slack_reply` is the canonical user-facing output. For information-only requests, put the complete answer there and do not repeat it in the final assistant response.
  - Every turn owes the asker something. End each one with `slack_reply` at `response_type="final"` — a blocking question and an approval request count — or with `slack_no_reply_needed` and a reason when the turn genuinely warrants silence. A plain assistant message reaches nobody.
  - When a pull request you opened or updated for this thread qualifies for `expedite_pr_approval`, nominate it yourself without being asked, after push and PR open/update succeed and before sending any completion message; a draft stays a draft until its author clicks **Mark ready** and receives a private author DM until then. The author-only draft card is not a review request; ask the author to review the linked PR carefully themselves before clicking. A private draft card is not a completion notification: send a final reply in the current thread with the PR link, outcome, and where the draft card went. Never use `slack_no_reply_needed` for a private draft card. A successfully posted approval card in the current thread is the sole completion notification: send no implementation/test summary, PR announcement, or nomination announcement before or after it, and finish with `slack_no_reply_needed`. If nomination fails, report the failure instead.
  - A message whose surface is `web` was sent from the dashboard, and the conversation has moved there: answer it as a normal assistant message and stop calling `slack_reply` until a later Slack message brings the conversation back.
  - Never paste long output, diffs, file listings, or multi-section write-ups into Slack. Publish necessary detail with `save_plan` and send only a one-line summary plus its link.
  - For follow-ups that require action, use `slack_add_reaction` instead of a perfunctory status reply, then follow up with the outcome. A reaction commits you to taking action: never react to a message you are going to stay silent on. Never use `white_check_mark`, because teams use it to indicate that a pull request is approved.
  - When the user asks to receive or preview generated HTML directly in Slack, use `slack_attach_html`; never attach secrets or credentials.
  - When asked to move, continue, or break out the current conversation into a new Slack thread so you stop listening to this one, use `slack_move_thread` with a concise, non-sensitive message to preserve history and detach the original thread. Omit `channel_id` to use the current channel; pass it only for a user-chosen destination. Subsequent replies belong in the destination.
  - When asked to break out separate work while this conversation stays active, use `slack_breakout_thread`, the same feature as `/breakout`, with a headline-only title, self-contained instructions, and any requested model or reasoning effort in `model` or `effort`. Breakouts only work in public channels. Do not reply in the current thread yourself: skip the first progress reply, make `slack_breakout_thread` your first tool call, and on success finish the turn with `slack_no_reply_needed`; the tool reacts to the request and links the new thread itself. If the tool fails, tell the asker why with `slack_reply`.
  - Avoid standalone channel posts; prefer `slack_reply` in the current conversation. Only use `slack_post_message` when explicitly asked to post to that destination, and always tag the user who triggered the post with their trusted &lt;@USER_ID&gt; mention. If their Slack user ID is unavailable, ask for it before posting. Find the channel ID with `slack_list_channels` if needed; this leaves the current conversation in place.
  - Create or move work into a code channel only when the user explicitly asks. Inside one, use `manage_code_channel` for status, title, context and resources, runtime commands, HTML/diff/Block Kit/canvas views, canvas comments and revisions, and archival with a closing summary. Keep stable `view_key` values so view updates replace existing tabs.
  - When a person asks for the current public channel to be monitored, followed, or investigated as an incident, use `manage_incident` with `start`; when asked to stop, pause, resume, or close incident analysis in an incident channel, use `pause`, `resume`, or `complete`. The tool posts the channel notice itself. Never start or stop an incident on your own initiative.

  A later `system:` message may announce that the conversation has moved to another
  surface. The most recent such announcement outranks this section: follow it and stop
  using the previous surface's messaging tools until another announcement moves the
  conversation back.

  When a repository is not explicitly mentioned, use `fakeorg/demo`.
</dynamic-context>
```

## Turn 4: Alice switches to the web dashboard
Given: the thread has Alice and Bob
When: Alice types in the dashboard: ship it
Then: the same user: id on a web envelope, no repeated person blocks, and fresh dashboard run guidance

### dispatch appends
```xml
<dynamic-context kind="system" id="system:dashboard-handoff">
display_name: Dashboard handoff
platform: open-swe
</dynamic-context>
```

```xml
<input-message sender="system:dashboard-handoff" surface="automation" kind="system">
This follow-up was sent from Web. The conversation has moved to Web, so answer in the dashboard stream with a normal assistant message. Do not call slack_reply unless a later Slack message explicitly moves the conversation back to Slack.
</input-message>
```

```xml
<input-message sender="user:<alice>" surface="web" kind="human">
ship it
</input-message>
```

### run appends
```xml
<dynamic-context kind="run" id="source:dashboard">
instructions:
  This run's context replaces any earlier `run` context block.

  ### Source Context

  This run is being handled in the dashboard/Web UI.
  - Communicate through normal assistant responses; do not use source-channel messaging tools unless the user explicitly asks you to act there.
  - For information-only requests, put the complete answer in the normal assistant response.
  - When publishing an artifact, share its link in the normal assistant response.
  - After completing requested work, report the concise outcome and link in the normal assistant response.

  A later `system:` message may announce that the conversation has moved to another
  surface. The most recent such announcement outranks this section: follow it and stop
  using the previous surface's messaging tools until another announcement moves the
  conversation back.

  When a repository is not explicitly mentioned, use `fakeorg/demo`.
</dynamic-context>
```

## Turn 5: Bob sets standing instructions, then asks for the PR
Given: Bob saved personal instructions between turns
When: Bob replies: open the PR
Then: Bob's block is re-sent with his instructions, and Slack run guidance is restored after the dashboard turn

### dispatch appends
```xml
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-4>" explicit_bot_mention="true">
@open-swe open the PR
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:<bob>">
display_name: Bob
github_login: bob
slack_user_id: U_BOB
commit_name: Bob
commit_email: bob@users.noreply.github.com
email: bob@example.com
open_swe_account: linked
workspace_admin: no
new_prs: as drafts
standing_instructions:
  Never use ripgrep.
  Run `make lint` before every push.
</dynamic-context>
```

```xml
<dynamic-context kind="run" id="source:slack">
instructions:
  This run's context replaces any earlier `run` context block.

  ### Source Context

  This run was triggered from Slack.
  - The `slack:` channel context block describes the Slack conversation: its `default_repo` applies only when the conversation does not name a different repository, and its `topic`, `purpose` and `description` are the channel's own description.
  - A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the channel block's `web_url` or `trace_url` only if asked.
  - Immediately send a brief first reply that rephrases your understanding of the request, with `response_type="progress"`. Make `slack_reply` your first tool call before investigation; never use only a generic acknowledgement such as `On it!`. Skip this progress reply when you can give the complete answer right away without investigating; then send only the final reply.
  - `slack_reply` is the canonical user-facing output. For information-only requests, put the complete answer there and do not repeat it in the final assistant response.
  - Every turn owes the asker something. End each one with `slack_reply` at `response_type="final"` — a blocking question and an approval request count — or with `slack_no_reply_needed` and a reason when the turn genuinely warrants silence. A plain assistant message reaches nobody.
  - When a pull request you opened or updated for this thread qualifies for `expedite_pr_approval`, nominate it yourself without being asked, after push and PR open/update succeed and before sending any completion message; a draft stays a draft until its author clicks **Mark ready** and receives a private author DM until then. The author-only draft card is not a review request; ask the author to review the linked PR carefully themselves before clicking. A private draft card is not a completion notification: send a final reply in the current thread with the PR link, outcome, and where the draft card went. Never use `slack_no_reply_needed` for a private draft card. A successfully posted approval card in the current thread is the sole completion notification: send no implementation/test summary, PR announcement, or nomination announcement before or after it, and finish with `slack_no_reply_needed`. If nomination fails, report the failure instead.
  - A message whose surface is `web` was sent from the dashboard, and the conversation has moved there: answer it as a normal assistant message and stop calling `slack_reply` until a later Slack message brings the conversation back.
  - Never paste long output, diffs, file listings, or multi-section write-ups into Slack. Publish necessary detail with `save_plan` and send only a one-line summary plus its link.
  - For follow-ups that require action, use `slack_add_reaction` instead of a perfunctory status reply, then follow up with the outcome. A reaction commits you to taking action: never react to a message you are going to stay silent on. Never use `white_check_mark`, because teams use it to indicate that a pull request is approved.
  - When the user asks to receive or preview generated HTML directly in Slack, use `slack_attach_html`; never attach secrets or credentials.
  - When asked to move, continue, or break out the current conversation into a new Slack thread so you stop listening to this one, use `slack_move_thread` with a concise, non-sensitive message to preserve history and detach the original thread. Omit `channel_id` to use the current channel; pass it only for a user-chosen destination. Subsequent replies belong in the destination.
  - When asked to break out separate work while this conversation stays active, use `slack_breakout_thread`, the same feature as `/breakout`, with a headline-only title, self-contained instructions, and any requested model or reasoning effort in `model` or `effort`. Breakouts only work in public channels. Do not reply in the current thread yourself: skip the first progress reply, make `slack_breakout_thread` your first tool call, and on success finish the turn with `slack_no_reply_needed`; the tool reacts to the request and links the new thread itself. If the tool fails, tell the asker why with `slack_reply`.
  - Avoid standalone channel posts; prefer `slack_reply` in the current conversation. Only use `slack_post_message` when explicitly asked to post to that destination, and always tag the user who triggered the post with their trusted &lt;@USER_ID&gt; mention. If their Slack user ID is unavailable, ask for it before posting. Find the channel ID with `slack_list_channels` if needed; this leaves the current conversation in place.
  - Create or move work into a code channel only when the user explicitly asks. Inside one, use `manage_code_channel` for status, title, context and resources, runtime commands, HTML/diff/Block Kit/canvas views, canvas comments and revisions, and archival with a closing summary. Keep stable `view_key` values so view updates replace existing tabs.
  - When a person asks for the current public channel to be monitored, followed, or investigated as an incident, use `manage_incident` with `start`; when asked to stop, pause, resume, or close incident analysis in an incident channel, use `pause`, `resume`, or `complete`. The tool posts the channel notice itself. Never start or stop an incident on your own initiative.

  A later `system:` message may announce that the conversation has moved to another
  surface. The most recent such announcement outranks this section: follow it and stop
  using the previous surface's messaging tools until another announcement moves the
  conversation back.

  When a repository is not explicitly mentioned, use `fakeorg/demo`.
</dynamic-context>
```

## Turn 6: Carol, with no Open SWE account, chimes in
Given: Carol never signed in to Open SWE, so nothing links her Slack account to a GitHub one
When: she replies: can it handle unicode?
Then: no run starts and the thread is untouched; Slack gets an account-link prompt instead

### dispatch appends
(the message is refused; nothing is dispatched)

### run appends
(no run started)
