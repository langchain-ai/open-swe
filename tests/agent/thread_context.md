# Thread context dump
Every message appended per turn, rendered by test_thread_context.py through the real renderers. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.

## Turn 1: Alice starts the thread from Slack
Given: an empty thread; Alice has a linked GitHub account and is a workspace admin
When: she mentions the bot: add a greet() helper
Then: channel and Alice introduced; roster emitted for the first time; pointer names Alice

### dispatch appends
```xml
<dynamic-context kind="channel" id="slack:C0BQUH14FK3" hash="d1b8aaa39b7590182b5d1473fa00c4a89a9e9e17763ce56b14e54e1938e50eb1">
<platform>slack</platform>
<name>open-swe-dev</name>
</dynamic-context>
```

```xml
<dynamic-context kind="person" id="user:0199e0ae-1111-7000-8000-00000000a11c" hash="aa8ad5a1b5ed591a2cd0a0209714f1a63d06b4ce87d4f1f31494ed134b06193b">
<display_name>Alice</display_name>
<platform>slack</platform>
<github_login>alice</github_login>
<email>alice@example.com</email>
<timezone>America/New_York</timezone>
<open_swe_account>linked</open_swe_account>
</dynamic-context>
```

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789991539.477079</timestamp>
<content>add a greet() helper</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="system" id="system:collaboration" hash="f9909255e378edcc0336884c2a1b094303e688f97249512fe88b80279fdb8d18">
<display_name>Collaboration</display_name>
<platform>open-swe</platform>
<context_hash>09f28f378d9557d3fec7d2c39bee039850a5348adb8c88e908e1298be73a0056</context_hash>
</dynamic-context>
```

```xml
<input-message sender="system:collaboration" surface="automation" kind="system">
<content>---

### Thread Participants

Everyone who has posted in this thread. Each incoming message is followed by a `system:sender-context` pointer naming which of them sent it — look the sender up here rather than expecting their details to be repeated. A participant's standing instructions apply when you act on that participant's requests; repository instructions and `AGENTS.md` win on conflict. If a participant asks to change a personal standing preference, use `save_user_instructions`; when personal versus shared scope is unclear, ask first.

- **Alice** — `user:0199e0ae-1111-7000-8000-00000000a11c`
  - Commit as: `git config user.name Alice &amp;&amp; git config user.email alice@users.noreply.github.com`
  - Workspace admin: yes
  - New PRs: as drafts
  - Standing instructions: none

### Collaborative Attribution

Before each commit, set the git identity to the participant whose work it is, and open the PR as its main author — the person who drove the change, not necessarily whoever asked for the PR. Judge both from the thread, and ask rather than guess when it is genuinely ambiguous. Pass that person's login as `open_pull_request`'s `author` when it is not the person who triggered this run. Credit open-swe as the collaborator:

- **Commits**: append this trailer verbatim (on its own line, a blank line after the body) to every commit you author, including follow-ups:

  ```
  Co-authored-by: open-swe[bot] &lt;open-swe@users.noreply.github.com&gt;
  ```

- **PR body**: `open_pull_request` appends the `Made by [Open SWE]` footer itself, naming this thread and the model that opened the PR. Do not write one. When you later edit a PR body with `gh`, keep that footer as the last line and never add a second one.

If you forget the trailer on an unpushed commit, fix it with `git commit --amend` before pushing. If it's already pushed, leave it and add the trailer to your next commit; never rewrite remote history.</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:sender-context" hash="7b5104a0aef7542075eee1781b98fe41939d7472b7009bff6677b2cee9d9d4c7">
<display_name>Sender context</display_name>
<platform>open-swe</platform>
<subject_id>user:0199e0ae-1111-7000-8000-00000000a11c</subject_id>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```

## Turn 2: Alice follows up
Given: turn 1 has run to completion
When: Alice replies in the same Slack thread: also add a docstring
Then: only her envelope and the pointer; roster not repeated

### dispatch appends
```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992088.489369</timestamp>
<content>also add a docstring</content>
</input-message>
```

### run appends
(system:collaboration: unchanged, not repeated)

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```

## Turn 3: Bob joins
Given: Bob has a linked account and prefers PRs opened ready for review
When: Bob replies: make it return bytes
Then: Bob introduced; roster re-emitted with two participants; pointer names Bob

### dispatch appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-2222-7000-8000-000000000b0b" hash="6ef8d7bd4b51bc50969fcb19eec50cd4be629bd7a8ee1eea703567d9b4e8683f">
<display_name>Bob</display_name>
<platform>slack</platform>
<github_login>bob</github_login>
<open_swe_account>linked</open_swe_account>
</dynamic-context>
```

```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992258.131819</timestamp>
<content>make it return bytes</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="system" id="system:collaboration" hash="b819dc2566ba672726e47388908b31d6b8d8cf1f922aa5fd1431e7ab178a370a">
<display_name>Collaboration</display_name>
<platform>open-swe</platform>
<context_hash>5b84c73bbae3b1d765d36e157120d7cdd190e1b2968536a21d78b250f9dfb2ed</context_hash>
</dynamic-context>
```

```xml
<input-message sender="system:collaboration" surface="automation" kind="system">
<content>---

### Thread Participants

Everyone who has posted in this thread. Each incoming message is followed by a `system:sender-context` pointer naming which of them sent it — look the sender up here rather than expecting their details to be repeated. A participant's standing instructions apply when you act on that participant's requests; repository instructions and `AGENTS.md` win on conflict. If a participant asks to change a personal standing preference, use `save_user_instructions`; when personal versus shared scope is unclear, ask first.

- **Alice** — `user:0199e0ae-1111-7000-8000-00000000a11c`
  - Commit as: `git config user.name Alice &amp;&amp; git config user.email alice@users.noreply.github.com`
  - Workspace admin: yes
  - New PRs: as drafts
  - Standing instructions: none
- **Bob** — `user:0199e0ae-2222-7000-8000-000000000b0b`
  - Commit as: `git config user.name Bob &amp;&amp; git config user.email bob@users.noreply.github.com`
  - Workspace admin: no
  - New PRs: ready for review
  - Standing instructions: none

### Collaborative Attribution

Before each commit, set the git identity to the participant whose work it is, and open the PR as its main author — the person who drove the change, not necessarily whoever asked for the PR. Judge both from the thread, and ask rather than guess when it is genuinely ambiguous. Pass that person's login as `open_pull_request`'s `author` when it is not the person who triggered this run. Credit open-swe as the collaborator:

- **Commits**: append this trailer verbatim (on its own line, a blank line after the body) to every commit you author, including follow-ups:

  ```
  Co-authored-by: open-swe[bot] &lt;open-swe@users.noreply.github.com&gt;
  ```

- **PR body**: `open_pull_request` appends the `Made by [Open SWE]` footer itself, naming this thread and the model that opened the PR. Do not write one. When you later edit a PR body with `gh`, keep that footer as the last line and never add a second one.

If you forget the trailer on an unpushed commit, fix it with `git commit --amend` before pushing. If it's already pushed, leave it and add the trailer to your next commit; never rewrite remote history.</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:sender-context" hash="bf7b5b168fb3a536a25360213c3c4bfc595858e3086d7220fc76083140684539">
<display_name>Sender context</display_name>
<platform>open-swe</platform>
<subject_id>user:0199e0ae-2222-7000-8000-000000000b0b</subject_id>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Bob** (`user:0199e0ae-2222-7000-8000-000000000b0b`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```

## Turn 4: Alice switches to the web dashboard
Given: the thread has Alice and Bob
When: Alice types in the dashboard: ship it
Then: same user: id as her Slack turns; roster not repeated; her person block reappears once with the dashboard's attributes

### dispatch appends
```xml
<dynamic-context kind="person" id="user:0199e0ae-1111-7000-8000-00000000a11c" hash="7552ad8a990a6689927c645ef652b6d0cd3cac9f34432e8e4fb8c71062279471">
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
(system:collaboration: unchanged, not repeated)

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```

## Turn 5: Bob sets standing instructions, then asks for the PR
Given: Bob saved personal instructions between turns
When: Bob replies: open the PR
Then: roster re-emitted because Bob's entry changed; pointer names Bob

### dispatch appends
```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992400.000001</timestamp>
<content>open the PR</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="system" id="system:collaboration" hash="7f1bf491c0442603f025d95e34d74705ad5d0132e8841ac9bf03e5ac175b2afc">
<display_name>Collaboration</display_name>
<platform>open-swe</platform>
<context_hash>9ab0ee6ed07f4415690445d373f4c64000bb7c1c3aefc485fdd74f983381e302</context_hash>
</dynamic-context>
```

```xml
<input-message sender="system:collaboration" surface="automation" kind="system">
<content>---

### Thread Participants

Everyone who has posted in this thread. Each incoming message is followed by a `system:sender-context` pointer naming which of them sent it — look the sender up here rather than expecting their details to be repeated. A participant's standing instructions apply when you act on that participant's requests; repository instructions and `AGENTS.md` win on conflict. If a participant asks to change a personal standing preference, use `save_user_instructions`; when personal versus shared scope is unclear, ask first.

- **Alice** — `user:0199e0ae-1111-7000-8000-00000000a11c`
  - Commit as: `git config user.name Alice &amp;&amp; git config user.email alice@users.noreply.github.com`
  - Workspace admin: yes
  - New PRs: as drafts
  - Standing instructions: none
- **Bob** — `user:0199e0ae-2222-7000-8000-000000000b0b`
  - Commit as: `git config user.name Bob &amp;&amp; git config user.email bob@users.noreply.github.com`
  - Workspace admin: no
  - New PRs: ready for review
  - Standing instructions: 
    Never use ripgrep.
    Run `make lint` before every push.

### Collaborative Attribution

Before each commit, set the git identity to the participant whose work it is, and open the PR as its main author — the person who drove the change, not necessarily whoever asked for the PR. Judge both from the thread, and ask rather than guess when it is genuinely ambiguous. Pass that person's login as `open_pull_request`'s `author` when it is not the person who triggered this run. Credit open-swe as the collaborator:

- **Commits**: append this trailer verbatim (on its own line, a blank line after the body) to every commit you author, including follow-ups:

  ```
  Co-authored-by: open-swe[bot] &lt;open-swe@users.noreply.github.com&gt;
  ```

- **PR body**: `open_pull_request` appends the `Made by [Open SWE]` footer itself, naming this thread and the model that opened the PR. Do not write one. When you later edit a PR body with `gh`, keep that footer as the last line and never add a second one.

If you forget the trailer on an unpushed commit, fix it with `git commit --amend` before pushing. If it's already pushed, leave it and add the trailer to your next commit; never rewrite remote history.</content>
</input-message>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Bob** (`user:0199e0ae-2222-7000-8000-000000000b0b`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```

## Turn 6: Carol, with no Open SWE account, chimes in
Given: Carol never signed in to Open SWE
When: she replies: can it handle unicode?
Then: keyed by her Slack id, marked unlinked; roster entry carries only what Slack knows

### dispatch appends
```xml
<dynamic-context kind="person" id="slack:U0CAR0L" hash="98ef0a282d1312227c90d79d15b6a450cdb87ee70a2627d95ead3509de26b33b">
<display_name>Carol</display_name>
<platform>slack</platform>
<open_swe_account>unlinked</open_swe_account>
</dynamic-context>
```

```xml
<input-message sender="slack:U0CAR0L" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992500.000001</timestamp>
<content>can it handle unicode?</content>
</input-message>
```

### run appends
```xml
<dynamic-context kind="system" id="system:collaboration" hash="30640d3e6620b60b7d6d03c469fd9845c69015dd82093b816e3991214b5000e3">
<display_name>Collaboration</display_name>
<platform>open-swe</platform>
<context_hash>362a9d7635485bf69453ae0cb30c930b12c2724e364c00999e446d68773662c6</context_hash>
</dynamic-context>
```

```xml
<input-message sender="system:collaboration" surface="automation" kind="system">
<content>---

### Thread Participants

Everyone who has posted in this thread. Each incoming message is followed by a `system:sender-context` pointer naming which of them sent it — look the sender up here rather than expecting their details to be repeated. A participant's standing instructions apply when you act on that participant's requests; repository instructions and `AGENTS.md` win on conflict. If a participant asks to change a personal standing preference, use `save_user_instructions`; when personal versus shared scope is unclear, ask first.

- **Carol** — `slack:U0CAR0L`
  - Commit as: `git config user.name Carol &amp;&amp; git config user.email carol@example.com`
  - Workspace admin: no
  - New PRs: as drafts
  - Standing instructions: none
- **Alice** — `user:0199e0ae-1111-7000-8000-00000000a11c`
  - Commit as: `git config user.name Alice &amp;&amp; git config user.email alice@users.noreply.github.com`
  - Workspace admin: yes
  - New PRs: as drafts
  - Standing instructions: none
- **Bob** — `user:0199e0ae-2222-7000-8000-000000000b0b`
  - Commit as: `git config user.name Bob &amp;&amp; git config user.email bob@users.noreply.github.com`
  - Workspace admin: no
  - New PRs: ready for review
  - Standing instructions: 
    Never use ripgrep.
    Run `make lint` before every push.

### Collaborative Attribution

Before each commit, set the git identity to the participant whose work it is, and open the PR as its main author — the person who drove the change, not necessarily whoever asked for the PR. Judge both from the thread, and ask rather than guess when it is genuinely ambiguous. Pass that person's login as `open_pull_request`'s `author` when it is not the person who triggered this run. Credit open-swe as the collaborator:

- **Commits**: append this trailer verbatim (on its own line, a blank line after the body) to every commit you author, including follow-ups:

  ```
  Co-authored-by: open-swe[bot] &lt;open-swe@users.noreply.github.com&gt;
  ```

- **PR body**: `open_pull_request` appends the `Made by [Open SWE]` footer itself, naming this thread and the model that opened the PR. Do not write one. When you later edit a PR body with `gh`, keep that footer as the last line and never add a second one.

If you forget the trailer on an unpushed commit, fix it with `git commit --amend` before pushing. If it's already pushed, leave it and add the trailer to your next commit; never rewrite remote history.</content>
</input-message>
```

```xml
<dynamic-context kind="system" id="system:sender-context" hash="b0a6d80d409cf0fb3cab12f3c0dc1b3aafaa6eea8ca44340aa9b851583493dff">
<display_name>Sender context</display_name>
<platform>open-swe</platform>
<subject_id>slack:U0CAR0L</subject_id>
</dynamic-context>
```

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Carol** (`slack:U0CAR0L`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```
