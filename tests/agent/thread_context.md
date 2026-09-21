# How a thread looks to the model

Rendered by `tests/agent/test_thread_context.py` through the real renderers
(`agent.input_messages`, `agent.prompt`, `agent.server.PrepareAgentRunMiddleware`);
the test fails if this file and the code disagree. Every block is the exact text
the model receives; only the Collaborative Attribution boilerplate is elided
after its first appearance.

Two layers append to the thread. **Dispatch** (Slack webhook / dashboard) adds a
person's `<dynamic-context>` introduction the first time they appear, then their
message as an `<input-message>` envelope whose `sender=` is their canonical
`user:<uuid>`. **The run** then adds the thread-level `system:collaboration`
roster — every participant's commit identity, permissions and standing
instructions — only when it has changed, and a one-line `system:sender-context`
pointer every turn.


## Turn 1 — Alice starts the thread from Slack

**Given** an empty thread; Alice has linked her GitHub account and is a workspace admin  
**When** she mentions the bot: *add a greet() helper*  
**Then** the channel and Alice are introduced, her message lands, the roster is emitted for the first time, and the pointer names her

**Dispatch appends:**

<sub>channel introduced once per thread · ~49 tokens</sub>

```xml
<dynamic-context kind="channel" id="slack:C0BQUH14FK3" hash="d1b8aaa39b7590182b5d1473fa00c4a89a9e9e17763ce56b14e54e1938e50eb1">
<platform>slack</platform>
<name>open-swe-dev</name>
</dynamic-context>
```

<sub>person introduced (dedupes on content hash) · ~95 tokens</sub>

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

<sub>the message; `sender` is the key the pointer uses · ~55 tokens</sub>

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789991539.477079</timestamp>
<content>add a greet() helper</content>
</input-message>
```

**The run then appends:**

<sub>`system:collaboration` — roster, once per thread · ~513 tokens</sub>

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

<sub>`system:sender-context` — the per-turn pointer · ~68 tokens</sub>

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```


## Turn 2 — Alice follows up

**Given** Turn 1 has run to completion  
**When** Alice replies in the same Slack thread: *also add a docstring*  
**Then** only her envelope and the pointer are added — nobody new, nothing about her has changed, so the roster is not repeated

**Dispatch appends:**

<sub>the message; `sender` is the key the pointer uses · ~55 tokens</sub>

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992088.489369</timestamp>
<content>also add a docstring</content>
</input-message>
```

**The run then appends:**

<sub>`system:collaboration`</sub> *(unchanged — not repeated)*

<sub>`system:sender-context` — the per-turn pointer · ~68 tokens</sub>

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```


## Turn 3 — Bob joins

**Given** Alice's two turns; Bob has a linked account and prefers PRs opened ready for review  
**When** Bob replies in the thread: *make it return bytes*  
**Then** Bob is introduced, the roster is re-emitted because it gained a participant, and the pointer names Bob

**Dispatch appends:**

<sub>person introduced (dedupes on content hash) · ~76 tokens</sub>

```xml
<dynamic-context kind="person" id="user:0199e0ae-2222-7000-8000-000000000b0b" hash="6ef8d7bd4b51bc50969fcb19eec50cd4be629bd7a8ee1eea703567d9b4e8683f">
<display_name>Bob</display_name>
<platform>slack</platform>
<github_login>bob</github_login>
<open_swe_account>linked</open_swe_account>
</dynamic-context>
```

<sub>the message; `sender` is the key the pointer uses · ~55 tokens</sub>

```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992258.131819</timestamp>
<content>make it return bytes</content>
</input-message>
```

**The run then appends:**

<sub>`system:collaboration` — roster (re-emitted only because it changed) · ~296 tokens</sub>

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

  …Collaborative Attribution rules, unchanged from Turn 1…
```

<sub>`system:sender-context` — the per-turn pointer · ~68 tokens</sub>

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Bob** (`user:0199e0ae-2222-7000-8000-000000000b0b`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```


## Turn 4 — Alice switches to the web dashboard

**Given** the thread now has Alice and Bob  
**When** Alice opens the thread in the dashboard and types *ship it*  
**Then** her envelope carries the same `user:` id as her Slack messages — one person, two surfaces — so the pointer resolves to the same roster entry and the roster is not repeated; her introduction reappears once because the dashboard knows different attributes about her

**Dispatch appends:**

<sub>person introduced (dedupes on content hash) · ~66 tokens</sub>

```xml
<dynamic-context kind="person" id="user:0199e0ae-1111-7000-8000-00000000a11c" hash="7552ad8a990a6689927c645ef652b6d0cd3cac9f34432e8e4fb8c71062279471">
<platform>github</platform>
<github_login>alice</github_login>
<email>alice@example.com</email>
</dynamic-context>
```

<sub>the message; `sender` is the key the pointer uses · ~34 tokens</sub>

```xml
<input-message sender="user:0199e0ae-1111-7000-8000-00000000a11c" surface="web" kind="human">
<content>ship it</content>
</input-message>
```

**The run then appends:**

<sub>`system:collaboration`</sub> *(unchanged — not repeated)*

<sub>`system:sender-context` — the per-turn pointer · ~68 tokens</sub>

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Alice** (`user:0199e0ae-1111-7000-8000-00000000a11c`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```


## Turn 5 — Bob sets standing instructions, then asks for the PR

**Given** Bob saved personal instructions in the dashboard between turns  
**When** Bob replies: *open the PR*  
**Then** the roster is re-emitted because Bob's entry changed, carrying his instructions; the pointer names Bob

**Dispatch appends:**

<sub>the message; `sender` is the key the pointer uses · ~53 tokens</sub>

```xml
<input-message sender="user:0199e0ae-2222-7000-8000-000000000b0b" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992400.000001</timestamp>
<content>open the PR</content>
</input-message>
```

**The run then appends:**

<sub>`system:collaboration` — roster (re-emitted only because it changed) · ~311 tokens</sub>

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

  …Collaborative Attribution rules, unchanged from Turn 1…
```

<sub>`system:sender-context` — the per-turn pointer · ~68 tokens</sub>

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Bob** (`user:0199e0ae-2222-7000-8000-000000000b0b`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```


## Turn 6 — Carol, who has no Open SWE account, chimes in

**Given** Carol is in the Slack channel but never signed in to Open SWE  
**When** she replies: *can it handle unicode?*  
**Then** she is keyed by her Slack id, marked `unlinked`, and gets a roster entry with only what Slack knows about her

**Dispatch appends:**

<sub>person introduced (dedupes on content hash) · ~62 tokens</sub>

```xml
<dynamic-context kind="person" id="slack:U0CAR0L" hash="98ef0a282d1312227c90d79d15b6a450cdb87ee70a2627d95ead3509de26b33b">
<display_name>Carol</display_name>
<platform>slack</platform>
<open_swe_account>unlinked</open_swe_account>
</dynamic-context>
```

<sub>the message; `sender` is the key the pointer uses · ~48 tokens</sub>

```xml
<input-message sender="slack:U0CAR0L" channel="slack:C0BQUH14FK3" surface="slack" kind="human">
<timestamp>1789992500.000001</timestamp>
<content>can it handle unicode?</content>
</input-message>
```

**The run then appends:**

<sub>`system:collaboration` — roster (re-emitted only because it changed) · ~362 tokens</sub>

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

  …Collaborative Attribution rules, unchanged from Turn 1…
```

<sub>`system:sender-context` — the per-turn pointer · ~61 tokens</sub>

```xml
<input-message sender="system:sender-context" surface="automation" kind="system">
<content>Sent by **Carol** (`slack:U0CAR0L`). Their commit identity, permissions and standing instructions are under Thread Participants.</content>
</input-message>
```


## What this buys

- **Per turn cost** is the envelope plus a ~40-token pointer. Everything about a person is stated once.
- **The roster re-emits only on change** — a new participant, or someone's settings or standing instructions changing. Nothing run-scoped lives in it: the PR footer, which names the model, is stamped by `open_pull_request` itself.
- **One person, one id.** Alice's Slack and dashboard messages share `user:…`, so the model never has to guess that two senders are the same human. Carol, who never signed in, keeps her surface id and is visibly `unlinked`.
- **Nothing expires.** The failure this replaces was a sender block emitted once and described as "this turn only"; three turns later the agent concluded it had no identity and refused to work.
