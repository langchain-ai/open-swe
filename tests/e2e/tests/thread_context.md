# Thread context dump
Every message the model is handed, turn by turn, recorded by thread_context.spec.ts driving the real server. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.

## Turn 1: Alice starts the thread from Slack
Given: an empty thread; Alice has a linked GitHub account and is a workspace admin
When: she mentions the bot: add a greet() helper
Then: dispatch introduces the channel and frames the mention, then the run adds the one block that describes her

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C_DEMO">
platform: slack
</dynamic-context>
```

```xml
<dynamic-context kind="system" id="system:slack-context">
display_name: Slack context
platform: slack
</dynamic-context>
```

```xml
<input-message sender="system:slack-context" channel="slack:C_DEMO" surface="slack" kind="system">
<content>You were mentioned in Slack.

## Default Repository Hint
fakeorg/demo
Use this only if the Slack conversation does not identify a different repository.

## Triggered by
Alice

## Slack Thread
- Channel ID: C_DEMO
- Channel name: #demo
- Thread TS: <slack-ts-1>
- Context starts at: the beginning of the thread
- Slack-provided channel description (topic/purpose; may specify the repository to operate in by default, but the conversation may specify any other repository):
  Demo channel topic
  Demo channel purpose

## Open SWE Links
- Web: http://127.0.0.1:3100/agents/<thread-id>
- A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the Web or trace URL above only if asked.</content>
</input-message>
```

```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-1>">
<content>add a greet() helper</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:<alice>">
display_name: Alice
github_login: alice
commit_name: Alice
commit_email: alice@users.noreply.github.com
email: alice@example.com
open_swe_account: linked
workspace_admin: yes
new_prs: as drafts
</dynamic-context>
```

## Turn 2: Alice follows up
Given: turn 1 has run to completion
When: Alice replies in the same Slack thread: also add a docstring
Then: her envelope and the Slack context around it; nothing about her has changed, so the run adds nothing

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C_DEMO">
platform: slack
</dynamic-context>
```

```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-2>">
<content>@open-swe add a greet() helper</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:open-swe">
display_name: open-swe
platform: slack
sender_type: self
</dynamic-context>
```

```xml
<input-message sender="system:open-swe" channel="slack:C_DEMO" surface="slack" kind="system" timestamp="<slack-ts-3>">
<content>On it! &lt;http://127.0.0.1:3100/agents/<thread-id>|Open in Web&gt;</content>
</input-message>
```

```xml
<input-message sender="system:open-swe" channel="slack:C_DEMO" surface="slack" kind="system" timestamp="<slack-ts-4>">
<content>✅ Done! I implemented the change and opened a PR: &lt;http://127.0.0.1:2024/mock/github/fakeorg/demo/pull/1|Add greet() helper&gt;

• Added `greet.py` with a `greet()` helper.
Let me know if you'd like any changes. &lt;http://127.0.0.1:3100/agents/<thread-id>|Open in Web&gt; • fake-scripted-model</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:slack-context">
display_name: Slack context
platform: slack
</dynamic-context>
```

```xml
<input-message sender="system:slack-context" channel="slack:C_DEMO" surface="slack" kind="system">
<content>You were mentioned in Slack.

## Default Repository Hint
fakeorg/demo
Use this only if the Slack conversation does not identify a different repository.

## Triggered by
Alice

## Slack Thread
- Channel ID: C_DEMO
- Channel name: #demo
- Thread TS: <slack-ts-1>
- Context starts at: the previous message where I was tagged
- Slack-provided channel description (topic/purpose; may specify the repository to operate in by default, but the conversation may specify any other repository):
  Demo channel topic
  Demo channel purpose

## Open SWE Links
- Web: http://127.0.0.1:3100/agents/<thread-id>
- A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the Web or trace URL above only if asked.</content>
</input-message>
```

```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-5>">
<content>also add a docstring</content>
</input-message>
```

### run appends
(everyone here is already described; nothing re-sent)

## Turn 3: Bob joins
Given: Bob has a linked account too, but is not a workspace admin
When: Bob replies: make it return bytes
Then: the run adds his block; Alice's is not re-sent

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C_DEMO">
platform: slack
</dynamic-context>
```

```xml
<dynamic-context kind="person" id="user:<alice>">
display_name: Alice
github_login: alice
open_swe_account: linked
</dynamic-context>
```

```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-5>">
<content>@open-swe also add a docstring</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:slack-context">
display_name: Slack context
platform: slack
</dynamic-context>
```

```xml
<input-message sender="system:slack-context" channel="slack:C_DEMO" surface="slack" kind="system">
<content>You were mentioned in Slack.

## Default Repository Hint
fakeorg/demo
Use this only if the Slack conversation does not identify a different repository.

## Triggered by
Bob

## Slack Thread
- Channel ID: C_DEMO
- Channel name: #demo
- Thread TS: <slack-ts-1>
- Context starts at: the previous message where I was tagged
- Slack-provided channel description (topic/purpose; may specify the repository to operate in by default, but the conversation may specify any other repository):
  Demo channel topic
  Demo channel purpose

## Open SWE Links
- Web: http://127.0.0.1:3100/agents/<thread-id>
- A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the Web or trace URL above only if asked.</content>
</input-message>
```

```xml
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-6>">
<content>make it return bytes</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:<bob>">
display_name: Bob
github_login: bob
commit_name: Bob
commit_email: bob@users.noreply.github.com
email: bob@example.com
open_swe_account: linked
workspace_admin: no
new_prs: as drafts
</dynamic-context>
```

## Turn 4: Alice switches to the web dashboard
Given: the thread has Alice and Bob
When: Alice types in the dashboard: ship it
Then: the same user: id on a web envelope, and nothing else — what she is does not depend on where she typed

### dispatch appends
```xml
<dynamic-context kind="system" id="system:dashboard-handoff">
display_name: Dashboard handoff
platform: open-swe
</dynamic-context>
```

```xml
<input-message sender="system:dashboard-handoff" surface="automation" kind="system">
<content>This follow-up was sent from Web. The conversation has moved to Web, so answer in the dashboard stream with a normal assistant message. Do not call slack_thread_reply unless a later Slack message explicitly moves the conversation back to Slack.</content>
</input-message>
```

```xml
<input-message sender="user:<alice>" surface="web" kind="human">
<content>ship it</content>
</input-message>
```

### run appends
(everyone here is already described; nothing re-sent)

## Turn 5: Bob sets standing instructions, then asks for the PR
Given: Bob saved personal instructions between turns
When: Bob replies: open the PR
Then: only Bob's block is re-sent, now carrying his instructions

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C_DEMO">
platform: slack
</dynamic-context>
```

```xml
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-6>">
<content>@open-swe make it return bytes</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:slack-context">
display_name: Slack context
platform: slack
</dynamic-context>
```

```xml
<input-message sender="system:slack-context" channel="slack:C_DEMO" surface="slack" kind="system">
<content>You were mentioned in Slack.

## Default Repository Hint
fakeorg/demo
Use this only if the Slack conversation does not identify a different repository.

## Triggered by
Bob

## Slack Thread
- Channel ID: C_DEMO
- Channel name: #demo
- Thread TS: <slack-ts-1>
- Context starts at: the previous message where I was tagged
- Slack-provided channel description (topic/purpose; may specify the repository to operate in by default, but the conversation may specify any other repository):
  Demo channel topic
  Demo channel purpose

## Open SWE Links
- Web: http://127.0.0.1:3100/agents/<thread-id>
- A compact Web footer is added automatically to Slack replies; do not duplicate it manually. Share the Web or trace URL above only if asked.</content>
</input-message>
```

```xml
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-7>">
<content>open the PR</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:<bob>">
display_name: Bob
github_login: bob
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

## Turn 6: Carol, with no Open SWE account, chimes in
Given: Carol never signed in to Open SWE, so nothing links her Slack account to a GitHub one
When: she replies: can it handle unicode?
Then: no run starts and the thread is untouched; Slack gets an account-link prompt instead

### dispatch appends
(the message is refused; nothing is dispatched)

### run appends
(no run started)
