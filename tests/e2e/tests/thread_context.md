# Thread context dump
Every message the model is handed, turn by turn, recorded by thread_context.spec.ts driving the real server. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.

## Turn 1: Alice starts the thread from Slack
Given: an empty thread; Alice has a linked GitHub account and is a workspace admin
When: she mentions the bot: add a greet() helper
Then: dispatch introduces the channel, carrying everything that stays true of the thread, then the run adds the one block that describes her

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C_DEMO">
platform: slack
name: #demo
thread_id: <slack-ts-1>
topic (untrusted): Demo channel topic
purpose (untrusted): Demo channel purpose
default_repo: fakeorg/demo
web_url: http://127.0.0.1:3100/agents/<thread-id>
</dynamic-context>
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
Then: her envelope alone — the channel is described, her turn-1 message and the bot's replies are already in the thread, and nothing about her has changed

### dispatch appends
```xml
<input-message sender="user:<alice>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-2>">
<content>also add a docstring</content>
</input-message>
```

### run appends
(everyone here is already described; nothing re-sent)

## Turn 3: Bob joins
Given: Bob has a linked account too, but is not a workspace admin
When: Bob replies: make it return bytes
Then: the run adds his block; Alice's is not re-sent, and dispatch does not describe her either

### dispatch appends
```xml
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-3>">
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
<input-message sender="user:<bob>" channel="slack:C_DEMO" surface="slack" kind="human" timestamp="<slack-ts-4>">
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
