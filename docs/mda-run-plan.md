# Deleting the per-run plan cache (`ToolSurface`)

Every MDA run calls back into the backend's `/agent-api/{endpoint}`. Today the
backend answers each call from a `RunSession`: the run as its graph factory
(`plan_agent`, `plan_reviewer`, `plan_review_scout`) planned it into a
`ToolSurface`, cached in memory on one pod (`RemoteGraph.session`,
`openswe/agent_api/graph.py`). `prepare` always plans again (`fresh=True`), and so
does any call that reaches another of the three pods. A plan reads settings, loads
the MCP catalog, may PATCH thread settings (forwarded to MDA), refreshes the GitHub
token, and on its first call reconnects the sandbox, refreshes the proxy and sets
the git identity.

In prod, thread 651120d3 made 82 `/agent-api` calls in 6 turns, and 18 of them
planned the run again. A trivial turn took about 13s around a 1.5s model call.

## Target

- Everything fixed for a run is worked out once, when the backend starts the run
  (`remote_run_context`, `openswe/agent_api/client.py`), and sent to MDA as run
  context. Model settings change only between runs.
- MDA owns the sandbox: reconnect, refresh, git identity and git evidence. The
  backend asks MDA when it needs something from the box.
- Everything else is a stateless call identified by the run token: backend-only
  tools by name and arguments, the inbox as a PostgreSQL read, and reporting
  (transcript, usage, tool calls, git evidence) as appends keyed by run id.
- `ToolSurface`, `RunSession`, `RunSessions`/`RemoteGraph.session` and the
  `plan_*` functions' surface output are deleted.

Classes used below:

- **(a)** worked out once at run start and sent in the run context
- **(b)** moved into MDA (`mda/rt`), as rules carried in (a) where it needs data
- **(c)** a stateless backend call keyed by the run token
- **(d)** deleted

## Inventory

### `ToolSurface` fields (`openswe/sandboxes/tool_runtime.py`)

| Field | Read by | Class |
| --- | --- | --- |
| `base_tools`, `dynamic` (MCP), `tools`, `integration_names`, `excluded`, `code_mode` | `catalog`, `operation`, `search_tools` | Schemas, exclusions and code-mode split: (a). Running a tool: (c), a registry built per process from the token's identity, not per run. `search_tools` BM25 over the catalog: (b). |
| `middleware` | every phase endpoint, `catalog` (`TaskCoordination`, `ModelCallLimit`, `Incident` flags) | Per middleware, below. |
| `models` (main, subagent, routes, fallback, vision, gateway) | `catalog`, `before_model` | (a) |
| `subagents` | `catalog` | (a) |
| `files` (composite: sandbox, skill routes, blob store) | `files.*`, `skills` | Sandbox: (b). Skill and blob routes: (c), routed by the token's identity. |
| `backend_paths`, `artifacts_root`, `skill_sources`, `file_checks` | `catalog` | (a) |
| `shell_guards` | `check_shell`, `operation` | (b) as rules, (c) for any check that needs GitHub |
| `inbox` | `prepare`, `inbox`, `inbox_received` | Now an `InboxSpec` (a); `ThreadInbox` reads PostgreSQL by thread id (c) |

### `RunSession` / `RemoteGraph` (`openswe/agent_api/graph.py`)

| Member | Class |
| --- | --- |
| `RemoteGraph.session`, `_sessions`, `end`, `_SESSION_TTL_SECONDS`, `_SESSION_LIMIT` | (d) |
| `RunSession.join`, `rejoin_preparation`, `flush`, `CarriedAcrossProcesses` | (d) once no middleware keeps memory |
| `RunSession.catalog`, `plan` | (a) — done in slice 1 |
| `RunSession.sandbox`, `bind_sandbox`, `sandbox_replaced`, `_reconnect_sandbox` | (b) |
| `RunSession.model_for`, `vision_for`, `_explicit_model` | (b) with routes and requested-model options from (a) |
| `RunSession.model_call` (`ShapesModelCalls`) | Rendered prompt: (a) at start. Hidden tools: (b) rules. |
| `RunSession.observe`, `_record_started`, `_record_finished` | (c) appends |
| `RunSession.operation`, `check_shell`, `record_evidence` | (c), guards (b) |
| `RemoteGraph.start_models` | (d) — done in slice 1 (the catalog carries the models) |

Users of the planners: `openswe/agent_api/{agent,reviewer,review_scout}.py`,
`openswe/server.py` (`plan_agent`), `openswe/reviewer.py` (`plan_reviewer`),
`openswe/review_scout/graph.py` (`plan_review_scout`),
`openswe/tools/search_tools.py` (`search_tools_tool(surface, …)`), and the tests in
`tests/agent/`, `tests/reviewer/test_agent_api.py`, `tests/sandbox/`,
`tests/middleware/`, `tests/mcp/`, `tests/models/`, `tests/threads/test_inbox.py`
and `tests/tools/test_mcp_sources.py`.

### Endpoints (`runtime_api`) and what they read

| Endpoint | Reads | Class |
| --- | --- | --- |
| `catalog` | whole plan, `ManagedSandbox.for_thread`, `TurnGitMetadata.actor` | (a) — slice 1; endpoint kept only for runs started before it |
| `prepare` | `bind_sandbox`, `inbox.receive(carried events)`, every `before_agent` hook, `TurnGitMetadata.begin` (`git_trace.py` in the box) | Sandbox, checkout, git baseline: (b). Rendered prompt, repo instructions: (a). Turn start, event receipt: (c). Title generation, model handoff classifier: (c). |
| `skills` | `skill_sources`, `files` | (c) by the token's identity (user skills by credential login) — done |
| `inbox`, `inbox_received` | `surface.inbox`, `rejoin_preparation` | (c), the catalog's `InboxSpec` sent with each read — done |
| `before_model` | `before_model` hooks (task coordination, GitHub proxy refresh, model routing, client tools), `ShapesModelCalls`, models | Proxy refresh: (b). Routing and requested model: (b) with (a) data, classifier as (c). Coordination messages: (c) through the inbox. Client-tools stop: (b). |
| `model_result` | `TranscriptMiddleware`, `RecordRunUsageMiddleware` | (c) on the graph's named reporters — done; fire-and-forget appends still to come |
| `after_model` | `RequireUserReplyMiddleware`, `RequireCliResultMiddleware` | (b), their tool names and retry limits in (a) |
| `after_agent` | usage, transcript end, step-limit notice, review-check settle, `TurnGitMetadata.reconcile` | (c) appends; git reconcile evidence collected by MDA (b) |
| `tool_started`, `tool_result` | transcript, `TurnGitMetadata.work_evidence` | (c) on the graph's named reporters — done; evidence collected by MDA (b) still to come |
| `sandbox_replaced` | `reconnect_replaced_sandbox` | (b); the backend records the new box id (c) |
| `sandbox_unreachable` | config only | (c) |
| `operation` | `surface.tools`, client tools, shell guards, `OperationFailure`, PR-target evidence, `rejoin_preparation` | (c) |
| `check_shell` | `ShellGuards` | (b), (c) for GitHub-backed checks |
| `record_evidence` | `IncidentMiddleware.observe` | (c), evidence kept in PostgreSQL |
| `files.*` | `surface.files` | (c) for backend paths, (b) for the box |
| `browser`, `launch_worker`, `worker_launched`, `adopt` | token only | already (c) |

### Middleware that keeps per-run state in memory

| Middleware | State | Goes to |
| --- | --- | --- |
| `BasePrepareRunMiddleware` (`PrepareAgentRunMiddleware`, reviewer, scout) | GitHub token and sandbox handle, rebuilt by `_rejoin` on every other pod | Token: resolved per call by identity (c). Sandbox handle: MDA (b). |
| `TranscriptMiddleware` | `_runs`: the turn, seen human ids, write queue and writer task (`aresume_run`/`aflush_run`) | PostgreSQL appends keyed by thread, run and turn; seen ids from `recorded_message_ids` |
| `ModelSelectionMiddleware` | built model objects for routes and requested models | (d); routes in (a), choice in run state |
| `ModelFallbackMiddleware` | registered fallbacks | (d); MDA does fallback from (a) |
| `IncidentMiddleware` | `IncidentSession.collector` (`EvidenceCollector`), lost across pods today | PostgreSQL, keyed by run |
| `TaskCoordinationMiddleware` | membership context, reloaded by its hooks | (c) |
| `RunSession._shell_guards` | none beyond the guard list | (b) |

## Migration order

Each slice ships alone; MDA and the backend deploy in either order, except where
noted for the brief window of one deploy.

Done:

1. **Catalog in the run context.** `plan_remote_run` plans the run and sends the
   catalog as `__catalog`; `adopt` returns the worker's own; a worker launch carries
   its coordinator's. MDA builds from it and calls `catalog` only for runs started
   without one. `RemoteGraph.start_models` and the `models` context key are gone.
2. **Inbox and skills without a plan.** The catalog carries an `InboxSpec` that MDA
   sends with each read; `ThreadInbox` belongs to the thread alone. `RunSkillStores`
   resolves a run's skill stores from its token. While an older MDA runs against
   this backend, its inbox reads come back empty and follow-ups wait for the next run.
3. **Reporting without a plan.** `model_result`, `tool_started` and `tool_result`
   run on the graph's named reporters (`RemoteGraph(reporters=…)`), whose state is
   keyed by run ids.

To do. A call that lands on another pod still plans the run until every call it
makes per model call and per tool call stops needing the plan:

4. **Loop phases as data.** `after_model` (reply and CLI-result nudges) and
   `before_model` (client-tool stop, task coordination, model routing, proxy
   refresh, call shaping, model choice) run from rules in the catalog. Open point:
   an incident run's Slack thread comes from its incident session, not the token, so
   the reply fallback needs it as run data.
5. **Sandbox to MDA.** Reconnect, proxy refresh, git identity, checkout and git
   evidence (`git_trace.py`) run in MDA; `prepare` stops touching the box;
   `sandbox_replaced` only records.
6. **Operations by name.** `operation`, `check_shell`, `record_evidence` and
   `files.*` resolve from a per-process registry and the token's identity.
7. **Preparation and the end of the run.** `prepare` and `after_agent` run from
   run data; incident evidence moves to PostgreSQL.
8. **Delete.** `catalog` endpoint, `RunSession`, `RemoteGraph.session`,
   `ToolSurface`, `CarriedAcrossProcesses`; the planners return the run context.
