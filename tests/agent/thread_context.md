# Thread context dump
Every message appended per turn, rendered by test_thread_context.py through the real renderers. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.

## Turn 1: Alice starts the thread from Slack
Given: an empty thread; Alice has a linked GitHub account and is a workspace admin
When: she mentions the bot: add a greet() helper
Then: channel and Alice introduced; Alice's participant block sent; pointer names Alice

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C0BQUH14FK3">
<platform>slack</platform>
<name>open-swe-dev</name>
</dynamic-context>
```

```xml
<dynamic-context kind="person" id="user:0199e0ae-1111-7000-8000-00000000a11c">
<display_name>Alice</display_name>
<platform>slack</platform>
<github_login>alice</github_login>
<email>alice@example.com</email>
<timezone>America/New_York</timezone>
<open_swe_account>linked</open_swe_account>
</dynamic-context>
```

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789991539.477079">
<content>add a greet() helper</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="participant" id="user:0199e0ae-1111-7000-8000-00000000a11c">
<display_name>Alice</display_name>
<git_identity>git config user.name Alice &amp;&amp; git config user.email alice@users.noreply.github.com</git_identity>
<workspace_admin>yes</workspace_admin>
<new_prs>as drafts</new_prs>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`).</content>
</input-message>
```

## Turn 2: Alice follows up
Given: turn 1 has run to completion
When: Alice replies in the same Slack thread: also add a docstring
Then: only her envelope and the pointer; nothing about her has changed

### dispatch appends
```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992088.489369">
<content>also add a docstring</content>
</input-message>
```

### run appends
(participants: all already visible, none re-sent)

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`).</content>
</input-message>
```

## Turn 3: Bob joins
Given: Bob has a linked account and prefers PRs opened ready for review
When: Bob replies: make it return bytes
Then: Bob introduced; only Bob's participant block sent, Alice's is not repeated; pointer names Bob

### dispatch appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-2222-7000-8000-000000000b0b">
<display_name>Bob</display_name>
<platform>slack</platform>
<github_login>bob</github_login>
<open_swe_account>linked</open_swe_account>
</dynamic-context>
```

```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992258.131819">
<content>make it return bytes</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="participant" id="user:0199e0ae-2222-7000-8000-000000000b0b">
<display_name>Bob</display_name>
<git_identity>git config user.name Bob &amp;&amp; git config user.email bob@users.noreply.github.com</git_identity>
<workspace_admin>no</workspace_admin>
<new_prs>ready for review</new_prs>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Bob** (`user:0199e0ae-2222-7000-8000-000000000b0b`).</content>
</input-message>
```

## Turn 4: Alice switches to the web dashboard
Given: the thread has Alice and Bob
When: Alice types in the dashboard: ship it
Then: same user: id as her Slack turns; no participant block re-sent; her person block reappears once with the dashboard's attributes

### dispatch appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-1111-7000-8000-00000000a11c">
<platform>github</platform>
<github_login>alice</github_login>
<email>alice@example.com</email>
</dynamic-context>
```

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" surface="web" kind="human">
<content>ship it</content>
</input-message>
```

### run appends
(participants: all already visible, none re-sent)

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`).</content>
</input-message>
```

## Turn 5: Bob sets standing instructions, then asks for the PR
Given: Bob saved personal instructions between turns
When: Bob replies: open the PR
Then: only Bob's participant block is re-sent, now with his instructions; pointer names Bob

### dispatch appends
```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992400.000001">
<content>open the PR</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="participant" id="user:0199e0ae-2222-7000-8000-000000000b0b">
<display_name>Bob</display_name>
<git_identity>git config user.name Bob &amp;&amp; git config user.email bob@users.noreply.github.com</git_identity>
<workspace_admin>no</workspace_admin>
<new_prs>ready for review</new_prs>
<standing_instructions>Never use ripgrep.
Run `make lint` before every push.</standing_instructions>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Bob** (`user:0199e0ae-2222-7000-8000-000000000b0b`).</content>
</input-message>
```

## Turn 6: Carol, with no Open SWE account, chimes in
Given: Carol never signed in to Open SWE
When: she replies: can it handle unicode?
Then: keyed by her Slack id, marked unlinked; only her participant block sent, with only what Slack knows

### dispatch appends
```xml
<dynamic-context kind="person" id="slack:U0CAR0L">
<display_name>Carol</display_name>
<platform>slack</platform>
<open_swe_account>unlinked</open_swe_account>
</dynamic-context>
```

```xml
<input-message sender="slack:U0CAR0L" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992500.000001">
<content>can it handle unicode?</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="participant" id="slack:U0CAR0L">
<display_name>Carol</display_name>
<git_identity>git config user.name Carol &amp;&amp; git config user.email carol@example.com</git_identity>
<workspace_admin>no</workspace_admin>
<new_prs>as drafts</new_prs>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Carol** (`slack:U0CAR0L`).</content>
</input-message>
```
