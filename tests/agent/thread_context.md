# Thread context dump
Every message appended per turn, rendered by test_thread_context.py through the real renderers. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.

## Turn 1: Alice starts the thread from Slack
Given: an empty thread; Alice has a linked GitHub account and is a workspace admin
When: she mentions the bot: add a greet() helper
Then: the channel is introduced, then her envelope, then the one block that describes her

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C0BQUH14FK3">
<platform>slack</platform>
<name>open-swe-dev</name>
</dynamic-context>
```

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789991539.477079">
<content>add a greet() helper</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-1111-7000-8000-00000000a11c">
<display_name>Alice</display_name>
<github_login>alice</github_login>
<commit_name>Alice</commit_name>
<commit_email>alice@users.noreply.github.com</commit_email>
<email>alice@example.com</email>
<open_swe_account>linked</open_swe_account>
<workspace_admin>yes</workspace_admin>
<new_prs>as drafts</new_prs>
</dynamic-context>
```

## Turn 2: Alice follows up
Given: turn 1 has run to completion
When: Alice replies in the same Slack thread: also add a docstring
Then: only her envelope; nothing about her has changed

### dispatch appends
```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992088.489369">
<content>also add a docstring</content>
</input-message>
```

### run appends
(everyone here is already described; nothing re-sent)

## Turn 3: Bob joins
Given: Bob has a linked account and prefers PRs opened ready for review
When: Bob replies: make it return bytes
Then: his envelope and his block; Alice's is not repeated

### dispatch appends
```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992258.131819">
<content>make it return bytes</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-2222-7000-8000-000000000b0b">
<display_name>Bob</display_name>
<github_login>bob</github_login>
<commit_name>Bob</commit_name>
<commit_email>bob@users.noreply.github.com</commit_email>
<open_swe_account>linked</open_swe_account>
<workspace_admin>no</workspace_admin>
<new_prs>ready for review</new_prs>
</dynamic-context>
```

## Turn 4: Alice switches to the web dashboard
Given: the thread has Alice and Bob
When: Alice types in the dashboard: ship it
Then: the same user: id on a web envelope, and nothing else — what she is does not depend on where she typed

### dispatch appends
```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" surface="web" kind="human">
<content>ship it</content>
</input-message>
```

### run appends
(everyone here is already described; nothing re-sent)

## Turn 5: Bob sets standing instructions, then asks for the PR
Given: Bob saved personal instructions between turns
When: Bob replies: open the PR
Then: only Bob's block is re-sent, now with his instructions

### dispatch appends
```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992400.000001">
<content>open the PR</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-2222-7000-8000-000000000b0b">
<display_name>Bob</display_name>
<github_login>bob</github_login>
<commit_name>Bob</commit_name>
<commit_email>bob@users.noreply.github.com</commit_email>
<open_swe_account>linked</open_swe_account>
<workspace_admin>no</workspace_admin>
<new_prs>ready for review</new_prs>
<standing_instructions>Never use ripgrep.
Run `make lint` before every push.</standing_instructions>
</dynamic-context>
```

## Turn 6: Carol, with no Open SWE account, chimes in
Given: Carol never signed in to Open SWE
When: she replies: can it handle unicode?
Then: keyed by her Slack id and marked unlinked; with no GitHub login she has no commit identity to author as

### dispatch appends
```xml
<input-message sender="slack:U0CAR0L" channel="slack:C0BQUH14FK3" surface="slack" kind="human" timestamp="1789992500.000001">
<content>can it handle unicode?</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="person" id="slack:U0CAR0L">
<display_name>Carol</display_name>
<open_swe_account>unlinked</open_swe_account>
<workspace_admin>no</workspace_admin>
<new_prs>as drafts</new_prs>
</dynamic-context>
```
