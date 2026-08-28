---
type: concept
title: Models, Profiles, Team Defaults & Instructions
description: How the agent resolves which LLM and reasoning effort a run uses (per-thread config over per-user profile over team default), how supported model IDs and effort/gateway/Fable rules are enforced, how models are constructed in agent/utils/model.py, and how per-repo versus per-user custom instructions are layered into a run.
tags: [models, reasoning-effort, profiles, team-defaults, instructions, model-selection, gateway, fable]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-27T06:27:22.313Z
sources:
  - id: openwiki-source-09b129ff728dd4990ea2f25e
    resource: repo://agent/dashboard/agent_instructions.py
  - id: openwiki-source-bd55a0c7231ffb3eb9e8ded0
    resource: repo://agent/dashboard/agent_overrides.py
  - id: openwiki-source-abba304194f5a40187cffde3
    resource: repo://agent/dashboard/options.py
  - id: openwiki-source-d9f679c15adbf4b3f612d406
    resource: repo://agent/dashboard/profiles.py
  - id: openwiki-source-23002b87792ed6949edb723b
    resource: repo://agent/dashboard/team_settings.py
  - id: openwiki-source-dc33a233b67bb1d08952543c
    resource: repo://agent/dashboard/thread_api.py
  - id: openwiki-source-9bf84d0c3d7e3b3001405497
    resource: repo://agent/dashboard/user_instructions.py
  - id: openwiki-source-10938886c8b24d0cdc72ad9e
    resource: repo://agent/prompt.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-f0db445078d7a8158aa93724
    resource: repo://agent/utils/gateway.py
  - id: openwiki-source-56ade344fdbe7d47c84f008f
    resource: repo://agent/utils/model.py
generated: { by: "openwiki/0.4.2", at: "2026-08-27T06:27:22.313Z" }
---

# Models, Profiles, Team Defaults & Instructions

Every agent run must pick a concrete `(model_id, reasoning_effort)` pair and load
the right custom instructions before it can start. Two independent layered
systems govern that: a **model/effort resolution chain** (team default → per-user
profile → per-thread config) and an **instruction layering** system (per-repo
instructions vs. per-user instructions, both subordinate to `AGENTS.md`). This
page explains where each layer lives, how they combine, and the provider-specific
rules that turn an abstract effort into concrete API kwargs.

Related: the run factory that consumes all of this lives in the
[agent graph / server](../architecture/agent-graph.md); model fallback and
per-request timeouts are applied through the
[middleware stack](../architecture/middleware-stack.md); and how instructions and
sender context are assembled into the prompt is part of
[context engineering](../workflows/context-engineering.md).

## Supported models, efforts, and the options registry

`agent/dashboard/options.py` is the single registry of what the workspace may
select. `SUPPORTED_MODELS` is a hand-maintained list of `ModelOption` records —
each with a provider-prefixed `id` (e.g. `anthropic:claude-opus-5`,
`openai:gpt-5.6-sol`, `fireworks:accounts/fireworks/models/kimi-k3`), a display
`label`, the `efforts` it accepts, a `default_effort`, and a `supports_images`
flag. The efforts list is per-model and non-uniform: some models omit `none`,
Kimi K3 only accepts `low`/`high`/`max`, and only a subset advertise `xhigh`/`max`.

`model_supports_effort` and `model_supports_images` gate every stored selection
against this registry, and `SUPPORTED_MODEL_IDS` is the frozen set every
resolution layer checks membership in. The global hardcoded fallback pair is
`DEFAULT_MODEL_ID` / `DEFAULT_MODEL_EFFORT`, surfaced through
`default_model_pair()`.

### Deprecated ids and same-provider fallback

Stored selections (profiles, team defaults, per-thread config, schedules) are not
discarded when a model id disappears. `DEPRECATED_MODEL_REPLACEMENTS` maps retired
ids onto their replacement, and `canonical_model_pair` applies that mapping,
preserving effort when the replacement supports it. For ids that merely dropped
out of the supported set (e.g. an Opus minor-version bump), `provider_fallback_pair`
keeps the selection on the same provider and, where possible, the same Claude
family, rather than silently falling through to the cross-provider global default.

## Model/effort resolution precedence

The authoritative resolution happens in the run factory in `agent/server.py`. It
starts from the team default and overrides upward, so the **later** a layer sits
in the chain, the higher its priority:

1. **Team default** — `get_team_default_model("agent")` seeds `(model_id, effort)`
   for the main model and its subagent.
2. **Per-user profile override** — if a triggering `github_login` resolves to a
   profile with a valid `(default_model, reasoning_effort)`, it replaces the team
   default (and by default drives the subagent too, unless the profile sets its
   own subagent pair).
3. **Stored thread settings** — a thread that already has a `model_id` reuses it.
4. **Per-thread run config** — an explicit `configurable.agent_model_id` /
   `agent_effort` is the one thing allowed to move a thread off its stored
   settings; the new choice is then persisted back onto the thread.

`agent_overrides.resolve_agent_model_id` documents and implements the same order
(`per-thread → profile → team default`) for callers that only need the model id,
and the dashboard's `thread_api._resolve_agent_model_choice` applies the identical
chain for interactive selection.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
  T["Team default<br/>get_team_default_model(agent)"] --> P{"Profile override<br/>valid and present?"}
  P -- yes --> PU["Use profile<br/>default_model/reasoning_effort"]
  P -- no --> TU["Keep team default"]
  PU --> S{"Stored thread<br/>model_id set?"}
  TU --> S
  S -- yes --> SU["Use stored thread settings"]
  S -- no --> SK["Keep resolved so far"]
  SU --> C{"Explicit per-thread<br/>agent_model_id valid?"}
  SK --> C
  C -- yes --> CU["Use per-thread choice<br/>then persist to thread"]
  C -- no --> CK["Keep resolved so far"]
  CU --> G["gate_fable_model"]
  CK --> G
  G --> M["provider_model_kwargs + make_model"]
```

Resolution precedence: each lower-priority source is overridden by a valid
higher-priority one before the Fable gate and model construction.

Every layer that produces a default runs its candidate through
`_resolve_default_pair`: use the pair if it is supported, else the same-provider
fallback, else the global default. This is why the factory always ends with a
valid, constructible pair even when the store is stale or a selection is unknown.

### Where each layer's state lives

- **Team defaults** live in a single LangGraph Store record keyed `"default"` in
  the `["team_settings"]` namespace (`agent/dashboard/team_settings.py`).
  `get_team_settings` merges the stored record over hardcoded defaults and is
  deliberately fail-soft: an unreachable store degrades to defaults rather than
  failing every run. Distinct roles resolve independently —
  `get_team_default_model` handles `agent`/`reviewer`/`chat` (with `chat`
  inheriting the agent default when unset), and there are dedicated resolvers for
  the diff-grouping pass and the thread-title model.
- **Per-user profiles** live in the `["profiles"]` namespace
  (`agent/dashboard/profiles.py`). Profiles hold `default_model`,
  `reasoning_effort`, an optional subagent pair, and non-model preferences such as
  `default_repo` and `draft_prs`. `agent_overrides.load_profile` reads them
  fail-soft on the run-start path (a store blip costs the run its per-user
  overrides, not the run itself), while dashboard reads use
  `profiles.get_profile` so failures surface.
- **Per-thread config** arrives on the run's `configurable` and is persisted into
  the thread's stored settings by the factory.

Profile writes are split from OAuth token writes on purpose: user-editable
settings live in `["profiles"]` and the encrypted GitHub token lives in
`["oauth_tokens"]`, so a profile save and a concurrent login/refresh cannot
clobber each other's fields.

## Reasoning effort → provider kwargs

An effort string is abstract; each provider expresses reasoning differently.
`agent/utils/model.py` translates a resolved effort into provider-specific kwargs
via `provider_model_kwargs`, which dispatches on the model id's provider prefix:

- **OpenAI** (`openai:`) → `openai_reasoning_for` returns a `reasoning` dict.
  Every non-`none` effort requests `summary: "auto"` so the Responses API emits
  visible reasoning text; `effort: "none"` disables reasoning and attaches no
  summary.
- **Anthropic** (`anthropic:`) → `anthropic_thinking_for` sets
  `{type: "adaptive", display: "summarized"}` (so summarized thinking is returned
  rather than the default omitted display), and `anthropic_effort_for` passes the
  effort through when it is one of `low/medium/high/xhigh/max`.
- **Google Gemini 3+** (`google_genai:` and `is_gemini_3_family`) →
  `google_thinking_level_for` maps effort onto `thinking_level`
  (`minimal/low/medium/high`, collapsing `high/xhigh/max` to `high`).
- **Fireworks** (`fireworks:`) → `fireworks_reasoning_effort_for` sets
  `model_kwargs.reasoning_effort`; the per-model `efforts` lists in `options.py`
  gate which values can reach it.
- **Baseten** (`baseten:`) → passes `reasoning_effort` through for
  `low/high/max`.

## Model construction: `make_model`

`make_model(model_id, use_gateway=..., **ModelKwargs)` builds the actual chat
model via LangChain's `init_chat_model`, applying provider-specific wiring:

- **Retries and timeout.** A default `max_retries` of `6` is applied (higher than
  the Anthropic SDK default so a 529 burst gets a fair chance before fallback),
  and a per-request `timeout` of `600s` is set for the known provider prefixes so
  a stalled connection becomes a retry instead of a wedged run.
- **OpenAI Responses API.** OpenAI models are configured to use the Responses API
  with `store: false`, `output_version: "responses/v1"`, and
  `include: ["reasoning.encrypted_content"]`. Desktop OpenAI OAuth is used when no
  `OPENAI_API_KEY` is set and the gateway is not applied.
- **Baseten.** Routed as an OpenAI-compatible provider; when the gateway is off it
  requires `BASETEN_API_KEY` and points at the Baseten base URL.
- **Context-window overrides.** `model_profile_with_context_override` injects a
  `profile` with the Codex context-window overrides (e.g. 272k for GPT-5.6
  variants) so LangChain's bundled models.dev profile does not undercount.
- **Caching.** Constructed models are cached keyed on `(model_id, use_gateway,
  max_tokens, frozen kwargs, event-loop id)`; `close_cached_models` tears them
  down.

### Gateway routing

Whether a model is routed through the LangSmith LLM Gateway is a tri-state:
`resolve_gateway_enabled` treats a team `gateway_enabled` of `True`/`False` as
authoritative and `None` as inheriting the `LANGSMITH_GATEWAY_ENABLED` deployment
default (`gateway_env_default`). When enabled, `gateway_overrides` supplies
`base_url`/`api_key`/`use_responses_api` that override the direct-provider
defaults. `make_model` resolves the deployment default itself when `use_gateway`
is `None`; async callers pass the team-resolved value.

### Cross-provider fallback

`fallback_model_id_for` returns a **cross-provider** fallback for the primary:
Anthropic primaries fall back to OpenAI and vice versa, and it returns `None` for
providers (Google, local, self-hosted) that should not be silently re-routed off
their host. The factory wires this into `ModelFallbackMiddleware` (see the
[middleware stack](../architecture/middleware-stack.md)), honoring an
`LLM_FALLBACK_MODEL_ID` env override first.

## Fable (ZDR) gating

`anthropic:claude-fable-*` models are gated by a workspace-wide `fable_enabled`
team toggle. `gate_fable_model` swaps a resolved Fable id for a safe non-Fable
Anthropic model (via `fable_disabled_fallback`) whenever Fable is disabled but a
Fable id reached the point of construction. This gate is applied at **every**
model-construction entrypoint — the run factory applies it to the main, subagent,
and title models after all precedence resolution, and the dashboard applies it in
`_resolve_agent_model_choice` — so a disabled Fable model can never reach
`make_model` regardless of which layer selected it.

## Two instruction stores

Custom instructions come from two independent stores that attach to a run at
different points, giving them different scope and lifetime.

### Per-repo instructions (system prompt)

`agent/dashboard/agent_instructions.py` stores admin-authored, per-repository
instructions in the `["agent_instructions"]` namespace, keyed by `owner/name`.
For a run, the factory resolves the effective repo and loads
`get_repo_agent_instructions`, storing the text as the thread's
`repo_instructions`. It is rendered into the **system prompt** via
`_render_repo_instructions_section` (`agent/prompt.py`) as "Repository-specific
Custom Instructions" with the same authority as the system prompt. Because it is
part of the system prompt, it applies to the whole thread, not a single turn.

### Per-user instructions (sender message)

`agent/dashboard/user_instructions.py` stores per-user instructions in the
`["user_instructions"]` namespace, keyed by GitHub login (capped at
`MAX_USER_INSTRUCTIONS_CHARS = 20_000`). These can be edited in the dashboard
Profile tab **or** written by the agent itself via the `save_user_instructions`
tool, which is why they live in their own namespace rather than on the profile
record — so agent-written updates and dashboard saves cannot clobber each other.
They are attached to the **triggering user's message** through
`construct_sender_context` and rendered by `_render_user_instructions_section` as
"Sender's Custom Instructions (user-level)", explicitly scoped to *only this
turn*.

### Conflict rule

The layering has a strict precedence for conflicts, stated in the prompt text
itself:

- `AGENTS.md` (read from the repo at runtime) is the highest authority; the system
  prompt tells the agent its contents override defaults with the same authority as
  the prompt.
- **Repository-specific custom instructions** are mandatory but yield to
  `AGENTS.md` on conflict.
- **User-level (sender) instructions** yield to *both* repository instructions and
  `AGENTS.md` on conflict, and apply only to the current turn.

So the effective ordering is `AGENTS.md > repo custom instructions > user-level
instructions`. Repo instructions ride in the shared system prompt; user
instructions ride with the sender's message and are per-turn and per-user.
