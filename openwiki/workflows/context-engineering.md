---
type: workflow
title: "Context Engineering: AGENTS.md, Source Context & Skills"
description: How the agent assembles context before and during a run — AGENTS.md injection into prompts and read_file results, source-context assembly from Slack/Linear/GitHub, and the layered Agent Skills mechanism served as virtual files.
tags: [context-engineering, agents-md, source-context, skills, prompt, middleware, deepagents]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-27T06:27:22.313Z
sources:
  - id: openwiki-source-63ebc853556c1b852ed80aff
    resource: repo://agent/analyzer.py
  - id: openwiki-source-d87936e6d54eab24f7479af1
    resource: repo://agent/baby_sit.py
  - id: openwiki-source-838cdb388dc01d838e2807cc
    resource: repo://agent/bundled_skills/baby-sit/SKILL.md
  - id: openwiki-source-8bcd61511ffd5619e6f47fad
    resource: repo://agent/bundled_skills/html-artifacts/SKILL.md
  - id: openwiki-source-fb23e4421b72cc55be83e96d
    resource: repo://agent/dashboard/skills.py
  - id: openwiki-source-cb4e403499865fd6b797127c
    resource: repo://agent/input_messages.py
  - id: openwiki-source-6a91255d02f2954f4233c8bb
    resource: repo://agent/middleware/subdir_agents.py
  - id: openwiki-source-10938886c8b24d0cdc72ad9e
    resource: repo://agent/prompt.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-db8a5812295508f44c54b439
    resource: repo://agent/source_context.py
  - id: openwiki-source-928adfe64cd7c30107b7c080
    resource: repo://agent/tools/organization_skills.py
  - id: openwiki-source-1065d81bfb1c5cfa92d5a291
    resource: repo://agent/tools/user_skills.py
  - id: openwiki-source-67ffc2016995f2003206500d
    resource: repo://agent/utils/agents_md.py
  - id: openwiki-source-ff16fde3cd496fd0b8de20da
    resource: repo://agent/utils/analyzer_skills.py
  - id: openwiki-source-25a50e8385de61204afe1bcf
    resource: repo://agent/webhooks/common.py
  - id: openwiki-source-021c9f7e0d1658b726348b52
    resource: repo://agent/webhooks/github.py
  - id: openwiki-source-eaf184b71081c2500012ddb3
    resource: repo://agent/webhooks/linear.py
  - id: openwiki-source-e8033e29419d205e5ac2fbb1
    resource: repo://agent/webhooks/slack.py
generated: { by: "openwiki/0.4.2", at: "2026-08-27T06:27:22.313Z" }
---

# Context Engineering: AGENTS.md, Source Context & Skills

"Context engineering" here means everything the system does to put the *right*
material in front of the model without the model having to hunt for it: the
repository's conventions (`AGENTS.md`), the provenance and history of the
triggering conversation (`source_context`), and reusable playbooks (Agent
Skills). These three mechanisms are largely independent, run at different points
in a run's lifecycle, and are consumed by both the main agent graph and the
reviewer/analyzer graphs.

See also: [architecture/agent-graph](../architecture/agent-graph.md),
[concepts/models-profiles-instructions](../concepts/models-profiles-instructions.md),
and [architecture/reviewer-and-analyzer](../architecture/reviewer-and-analyzer.md).

## AGENTS.md: repository conventions

`AGENTS.md` (with `CLAUDE.md` as a legacy fallback) is a repo's short
conventions document. The system treats its contents as mandatory rules with the
same authority as the system prompt, and gets them into context through three
distinct paths depending on which graph is running.

### Reviewer: deterministic fetch from GitHub

The reviewer does not rely on the model to clone a repo and read the file. It
fetches `AGENTS.md` directly from the GitHub Contents API at the PR's base SHA
via `fetch_agents_md`, trying `AGENTS.md` then `CLAUDE.md` in order. Only a 404
falls through to the next filename; any other non-200 status (or an oversize
file above the 64 KiB cap) returns `None` so the reviewer never enforces stale
rules from a secondary file.

In addition to the root file, the reviewer loads *directory-scoped* `AGENTS.md`
files for the changed files in the diff. `applicable_agents_md_paths` computes
every ancestor-directory `AGENTS.md` for the changed files, ordered shallowest
to deepest so more deeply nested instructions can take precedence, and
`fetch_scoped_agents_md` fetches them concurrently (bounded by a semaphore) with
each candidate failing independently. Both the root and scoped contents are
awaited alongside the other reviewer context tasks and folded into the reviewer
prompt.

### Main agent: model reads it, middleware reinforces it

For the main agent the system prompt instructs the model to read `AGENTS.md` at
the repo root in full immediately after syncing/cloning, and states its rules
override defaults. The prompt also encodes the precedence order used elsewhere:
repository-specific custom instructions and environment instructions are
mandatory, but `AGENTS.md` wins when they conflict.

`SubdirAgentsReadMiddleware` reinforces subdirectory conventions at read time.
It wraps `read_file` tool calls: after a successful read of a file under some
directory, it loads the ancestor `AGENTS.md` files for that path from the run's
sandbox backend and appends them to the tool result inside a
`<system-reminder>` block, telling the model to follow them before editing and
that more deeply nested instructions take precedence. It tracks which
`AGENTS.md` paths it has already surfaced per thread so each is injected at most
once, treats a direct read of an `AGENTS.md` file as already-loaded, and skips
non-UTF-8 or oversize content (truncating above 64 KiB). Failures to read a
candidate are swallowed so a missing ancestor file never breaks the underlying
read.

## Source context: where a run came from

`SourceContext` (in `agent/source_context.py`) is the small typed record that
answers "which Slack thread / Linear issue / GitHub issue / PR started this
run?". It rides along in LangGraph thread metadata and in the baby-sit watch
record and is read across many modules.

Two design rules make it robust as a long-lived, multi-writer record:

- **Unknown keys survive.** Every model uses `extra="allow"`, and `dump()`
  serializes with `exclude_unset=True`, so a call site that reads a context,
  enriches it (e.g. adds a Slack permalink or a `breakout_from` marker), and
  writes it back produces a byte-identical round-trip for the keys it did not
  touch — a plain `model_dump` would inject defaults for every omitted field.
- **Parsing never raises.** Thread metadata is written by webhooks and older
  deployments and is not validated on write, so `SourceContext.parse` returns an
  empty context on any `ValidationError`: losing the provenance of a run is
  better than failing the run.

Webhooks populate `source_context` at run-creation time — Slack
(`SlackThreadRef`), Linear (`LinearIssueRef`), and GitHub (`GitHubIssueRef` /
`pr_number`) — and store it in the thread metadata under the `source_context`
key. The prompt layer then renders surface-specific guidance (Slack, Linear,
GitHub, dashboard) so the agent knows how to communicate back through the
originating channel.

### Full history passed up front

The provenance record is only the pointer; the actual conversation history is
serialized into the run's input messages up front rather than left for the agent
to fetch. The Linear webhook, for example, pulls the issue's `comments` from the
GraphQL payload and includes the relevant tail — the comments from the
triggering comment onward, or the recent comments filtered of the bot's own
prior replies — as structured input messages, so the model sees the whole
relevant issue thread from its first turn. Slack and GitHub triggers assemble
their thread/issue histories the same way.

### Structured input messages

`agent/input_messages.py` builds the application-owned model inputs as XML-ish
envelopes rather than raw strings. `build_run_input` / `build_input_messages`
emit `<input-message>` envelopes carrying `sender`, `surface`, `kind`, optional
`channel`, and structured `<data>` fields, preceded by `<dynamic-context>`
entity introductions for the people, channels, and system identities involved.
Untrusted entity fields (channel `topic`/`purpose`) are marked
`trust="untrusted"`. This gives the model a consistent, attributable view of who
is speaking and from where.

Each `<dynamic-context>` block is content-hashed. `filter_new_dynamic_contexts`
and the `injected_dynamic_context_hashes` metadata key let the system introduce
an entity's context exactly once across a multi-turn thread instead of repeating
it every run. Because summarization can drop a context block behind its
`cutoff_index` while the record still sits in state, `visible_dynamic_context_hashes`
computes only the hashes still visible to the model (messages from the cutoff
onward) so a context the model can no longer see is reintroduced. The system
prompt itself is wrapped by `wrap_system_prompt` into a
`<system-instructions format="open-swe-v1">` envelope, with environment/instruction
additions appended as serialized system messages.

## Agent Skills

Skills are reusable playbooks ("read this before doing X") delivered through
deepagents' **progressive disclosure** mechanism: the agent is told a skill's
name and description up front and reads the full `SKILL.md` only when the task
matches. Skills are served to the agent as **virtual files** — the model reaches
them with ordinary `read_file` calls under a route prefix — so nothing is ever
written into the execution sandbox.

### CompositeBackend routing

The mechanism is a `CompositeBackend` whose `default` is the run's real backend
(the sandbox or the user's project) and whose `routes` map route prefixes to
skill-serving backends. The composite strips the route prefix before delegating,
so a backend seeded with `/<skill>/SKILL.md` serves the agent-facing path
`/<route>/<skill>/SKILL.md`. The route prefixes are passed to
`create_deep_agent(..., skills=[...])`, which is what tells deepagents'
`SkillsMiddleware` where to discover skills and advertise them.

### Bundled skills

Two skills ship with the repo under `agent/bundled_skills/` and are always
mounted, read-only, at `/bundled-skills/`:

- **baby-sit** — monitor a GitHub PR until CI is green, diagnose failures, and
  rerun only evidence-backed flaky Actions jobs.
- **html-artifacts** — how to author the self-contained HTML for `save_plan`,
  `output_iframe`, and `slack_attach_html`.

They are served through a `ReadOnlyBackend` wrapping a `FilesystemBackend`
rooted at the bundled-skills directory in `virtual_mode`.

### Organization and user skills

The main agent layers additional skill sources on top of the bundled ones:

- **User skills** at `/skills/` — per-user `SKILL.md` files stored in the
  LangGraph store under the `user_skills` namespace keyed by GitHub login, served
  via a read-only `StoreBackend`. This route is inserted at the front of the
  skill sources so a user's own skills take priority.
- **Organization skills** at `/organization-skills/` — workspace-wide skills in
  the `organization_skills` namespace, loaded into every user's runs and served
  read-only.

Skills are persisted through `agent/dashboard/skills.py`, which stores each skill
as a virtual `SKILL.md` (a YAML `name`/`description` front-matter block plus the
instructions body) under the key `/<name>/SKILL.md`. Names must match
`^[a-z0-9]+(?:-[a-z0-9]+)*$`; descriptions and instructions have length caps
(1 KiB and 20 KiB), and organization skills are capped at 1000. Agents create and
edit these skills through tools: `save_user_skill` / `delete_user_skill`
(`agent/tools/user_skills.py`) resolve the triggering user's login and cannot
touch another user's or a bundled skill, while `save_organization_skill` /
`delete_organization_skill` (`agent/tools/organization_skills.py`) are gated to
workspace admins via `require_admin` because they change every user's runs. Skill
instructions are always a full replacement of the body, never a delta.

### Desktop runs

A desktop run does not have an organization store or a per-user GitHub login.
Instead it mounts the user-skills route as a `ReadOnlyBackend(StateBackend())`
(skills carried in run state), skips the organization route, and adds separate
artifact routes so the agent's scratch files never land in the user's project
checkout (the default backend).

### Analyzer skills

The style analyzer graph uses the same virtual-file trick for its two bundled
playbooks in `agent/skills/` — `bootstrap-repo-analysis` and
`continual-learning`. They are mounted at `/skills/` via a `StateBackend` inside
a `CompositeBackend`, and `build_skill_files` seeds the run input's `files`
channel with the *stripped* keys (`/<skill>/SKILL.md`). `skill_path_for_mode`
selects which playbook the analyzer follows based on the run mode
(`bootstrap` vs `continual`), and the agent addresses it under `/skills/...`.
Because the skills live in state, nothing is written to the analyzer's sandbox.
