# TypeScript Backend Monorepo Plan

## Goal

Replace the backend, graph implementations, operational scripts, and backend tests with
TypeScript. The dashboard and desktop applications remain in the same pnpm workspace. The final
tree has no Python source, Python test harness, Python package metadata, or Python runtime in any
development, deployment, or desktop path.

This is a behavioral port, not a file-by-file translation. The existing graph IDs, HTTP routes,
webhook behavior, store namespaces, thread metadata, tool names and schemas, sandbox recovery
rules, and user-visible outcomes remain compatible through the cutover.

## Design rules

1. Use Node.js 24.18.1, ESM, strict TypeScript, pnpm, Turborepo, Vitest, and Zod 4.
2. Build graphs with `deepagents` and the JavaScript LangGraph packages, following the local
   `~/src/deepagentsjs` reference.
3. Make the existing TanStack Start application the single authored and public backend. Use server
   functions for app-internal calls and server routes for webhooks, OAuth, streaming, binary
   responses, and compatibility endpoints.
4. Put behavior behind domain-oriented modules. Do not reproduce the current `utils/` and
   one-tool-per-file layout as workspace packages.
5. Keep wire schemas in one leaf package and derive TypeScript types from Zod schemas.
6. Define ports at true external seams only. Production adapters and in-memory test adapters make
   those seams real.
7. Keep package exports explicit. Internal files are not importable through wildcard exports.
8. Tests exercise each module through its interface. Replace implementation-shaped Python tests
   rather than transliterating them.
9. Preserve environment variable names during the port. Rename configuration only after parity.
10. Do not split live traffic between two implementations. Each migration slice can be compared
    offline, but `langgraph.json` points to one complete runtime at a time.
11. Use Oxlint and Oxfmt across the new packages so the backend joins one workspace-wide
    TypeScript toolchain.
12. Verify every new dependency's license before installation. Add compatible, exact versions with
    pnpm after checking the local Deep Agents and LangGraph peer ranges; do not independently take
    `latest` across the LangChain package family.

## Compatibility contract

Compatibility is exact at observable seams. It does not mean comparing randomized ciphertext or
irrelevant JSON property order byte-for-byte.

| Seam | Required compatibility | Permanent fixture |
|---|---|---|
| Store | Identical namespace segments, keys, index flags, null/not-found behavior, and JSON value shapes | One old-runtime record per schema plus search/delete cases |
| Thread metadata | Identical keys, merge rules, ownership fields, frozen settings, and timestamps | Before/after metadata snapshots for each ingress source |
| Deterministic IDs | Identical UUID5/SHA-derived strings for GitHub, Slack, Linear, reviewer, schedule, and finding inputs | Input-to-ID vectors |
| Encrypted tokens | TypeScript decrypts existing Fernet tokens; a rollback runtime can decrypt newly written tokens; key rotation order is preserved | Fixed plaintext/ciphertext/key-ring vectors in both directions |
| JWT and cookies | Identical algorithms, claims, expiry rules, names, paths, SameSite/Secure/HttpOnly behavior, and invalid-token outcomes | Fixed-clock token vectors plus Set-Cookie snapshots |
| Durable runs | Identical assistant/graph selection, configurable values, metadata, multitask strategy, durability, stream modes, webhook, event-streaming flags, and prepared run ID | Captured SDK request payloads |
| HTTP | Identical public paths, methods, auth rules, status codes, redirects, headers, cookies, JSON shapes, SSE event IDs, and binary filenames | Request/response contract fixtures by route group |
| Webhooks | Identical signature validation, dedupe, deterministic routing, acknowledgement timing, queued-run decisions, and outbound reactions | Signed vendor payload fixtures and normalized-event snapshots |
| Tools | Identical registered names, descriptions, input schemas, availability rules, and structured output shapes | Tool registry snapshots for every graph/run mode |
| Graph state | Existing checkpoints resume without losing required state; JSON-only state remains serializable | Representative serialized state and resume scenarios |
| Sandboxes | Identical reconnect/create/replace rules, provider IDs, proxy refresh, git identity, and unreachable error behavior | Provider contract suite plus lifecycle state tables |

Generate fixtures with the old runtime before deleting it. The generator is migration-only and is
removed at cutover; fixture consumers remain TypeScript forever.

## Target workspace

```text
.
├── apps/
│   ├── graphs/                 # graph exports and execution-plane composition
│   └── ops/                    # snapshot, cron, and maintenance commands
├── packages/
│   ├── contracts/              # shared wire schemas and stable identifiers
│   ├── runtime/                # threads, runs, store, dispatch, tracing
│   ├── auth/                   # identity, sessions, credentials, encryption
│   ├── workspace/              # sandbox lifecycle and repository workspace
│   ├── integrations/           # GitHub, Slack, Linear, Notion, MCP, HTTP
│   ├── agent/                  # main and chat graphs, tools, middleware
│   ├── review/                 # reviewer, analyzer, findings, style learning
│   ├── automation/             # scheduler, watches, reconciliation, background work
│   ├── control-plane/          # dashboard use cases and authorization
│   └── test-support/           # private fakes, fixtures, contract harnesses
├── ui/                         # TanStack Start UI + the single application backend
├── desktop/                    # existing desktop application
├── tests/e2e/                  # Playwright only; TypeScript fixtures and harness
├── evals/reviewer/             # TypeScript evaluation runners + language-neutral datasets
├── langgraph.json
├── package.json
├── pnpm-workspace.yaml
└── turbo.json
```

`ui` stops proxying to a separately authored application backend. Its TanStack Start server owns
SSR, sessions, middleware, dashboard operations, OAuth, webhooks, and streaming. Business behavior
stays in packages; route files only bind TanStack's HTTP interfaces to those modules.

`apps/graphs` is deliberately thin. It exports deployable graphs and constructs their dependencies.
The graph server remains an execution plane for durable threads, runs, queues, checkpoints, crons,
and the long-term store. It is not a second home for application routes or business behavior.

## Dependency direction

```text
contracts
   │
   ├── runtime
   ├── auth ───────────────┐
   ├── workspace           │
   └── integrations ◄──────┘
          │
          ├── agent
          ├── review
          └── automation
                 │
       agent ────┼──── review
          \      │      /
           \ control-plane
            \    │    /
               ui

agent + review + automation ── apps/graphs

apps/ops ── runtime + workspace + automation
ui ─────── contracts + auth + control-plane + integrations + runtime
desktop ── ui build artifact + local graph runtime
```

The graph packages do not import `control-plane` or `ui`. Inbound integration code
dispatches runs through `runtime`; it does not import graph implementations. This prevents the
current webhook/graph/dashboard dependency loops from crossing package seams.

## Package modules and interfaces

### `@open-swe/contracts`

This is the dependency leaf shared by backend packages and the dashboard.

Owns:

- branded identifiers for thread, run, sandbox, repository, pull request, and user identity;
- Zod schemas for thread metadata, configurable run input, content blocks, findings, plans,
  schedules, environments, profiles, review records, and HTTP payloads;
- stable enums for source channel, graph ID, run state, review state, and model effort;
- JSON-safe value helpers and schema version fields.

Interface:

```ts
export const ThreadMetadataSchema: z.ZodType<ThreadMetadata>
export const RunConfigSchema: z.ZodType<RunConfig>
export const DashboardRouteSchemas: RouteSchemaRegistry
export type JsonValue = ...
```

It does not read environment variables, perform I/O, import SDK clients, or contain business
rules. Route response schemas become the shared contract with `ui`.

### `@open-swe/runtime`

This module hides LangGraph SDK mechanics and the deployment store behind one interface. It owns
thread creation and metadata updates, durable run dispatch, command and event streaming, cron
operations, store namespace access, run usage, trace links, and completion callbacks.

Interface:

```ts
export interface Runtime {
  threads: ThreadRepository
  runs: RunRepository
  records: RecordStore
  crons: CronRepository
  dispatch(request: DispatchRequest): Promise<DispatchedRun>
}

export function createRuntime(config: RuntimeConfig): Runtime
export function createInMemoryRuntime(seed?: RuntimeSeed): Runtime
```

Invariants hidden by the implementation:

- deterministic thread IDs remain stable across all ingress channels;
- thread metadata updates are merge-safe;
- dispatch creates or reuses the thread before starting a durable run;
- store namespaces and record versions remain backward compatible;
- completion webhooks and run configuration are applied consistently.

The SDK-backed adapter is production; the in-memory adapter supports module and route tests.

### `@open-swe/auth`

This module owns identity resolution and secret custody. It absorbs dashboard sessions, GitHub and
OpenAI OAuth state, OIDC, Slack and Notion connection state, user mappings, team/user credentials,
token refresh, encryption, CSRF, and repository membership gates.

Interface:

```ts
export interface IdentityManager {
  authenticate(request: Request): Promise<Principal | null>
  requirePrincipal(request: Request): Promise<Principal>
  resolveRunIdentity(input: RunIdentityInput): Promise<RunIdentity>
}

export interface CredentialVault {
  get(subject: CredentialSubject, kind: CredentialKind): Promise<Credential | null>
  put(subject: CredentialSubject, credential: Credential): Promise<void>
  delete(subject: CredentialSubject, kind: CredentialKind): Promise<void>
}
```

Encrypted store records are preserved byte-for-byte where possible. OAuth providers are internal
adapters; callers see normalized identities and credentials.

### `@open-swe/workspace`

This module owns the complete sandbox and checkout lifecycle. It replaces the scattered sandbox
provider, cache, proxy, repo-preparation, checkpoint, diff, recovery, and terminal logic.

Interface:

```ts
export interface WorkspaceManager {
  ensure(request: EnsureWorkspaceRequest): Promise<Workspace>
  recreate(request: RecreateWorkspaceRequest): Promise<Workspace>
  inspect(threadId: ThreadId): Promise<WorkspaceStatus>
  release(threadId: ThreadId): Promise<void>
}

export interface Workspace {
  readonly id: SandboxId
  execute(command: Command): Promise<CommandResult>
  files: WorkspaceFiles
  repository: RepositoryWorkspace
}
```

The implementation hides provider selection, reconnect versus create, the unreachable-sandbox
error, reviewer-only replacement, GitHub proxy refresh, git identity, snapshots, turn checkpoints,
safe refs, downloads, terminal connection, and output offloading.

Provider adapters live under `src/providers/` and implement the Deep Agents sandbox protocol.
Start with LangSmith and local adapters because both are required for production and tests. Port
Daytona, Modal, Runloop, and E2B behind the same internal provider seam without exposing provider
types to callers. Run `@langchain/sandbox-standard-tests` where an adapter supports the standard
suite, plus app-specific reconnect, replacement, proxy, timeout, and cleanup tests.

### `@open-swe/integrations`

This package contains several deep modules sharing external HTTP, signing, retry, and rate-limit
infrastructure. It is one package to avoid a workspace package per vendor, but each vendor has an
explicit export path.

Exports:

```text
@open-swe/integrations/github
@open-swe/integrations/slack
@open-swe/integrations/linear
@open-swe/integrations/notion
@open-swe/integrations/observability
@open-swe/integrations/web
```

Each module owns payload verification, external data normalization, retry/error translation, and
outbound operations. GitHub additionally owns App installation tokens, repository access, git
proxy configuration, pull requests, comments, checks, CI state, and review feedback. Slack and
Linear own channel-specific thread context and notification formatting.

Inbound handlers return normalized events or dispatch through an injected `Runtime`; graph code
never parses raw webhook payloads. Agent tools use injected vendor clients; the tools do not issue
HTTP requests themselves.

### `@open-swe/agent`

This module owns the main graph and the general chat graph. Its small external interface creates a
graph from configured dependencies.

Interface:

```ts
export interface AgentDependencies {
  runtime: Runtime
  workspaces: WorkspaceManager
  identity: IdentityManager
  credentials: CredentialVault
  integrations: AgentIntegrations
  models: ModelCatalog
}

export function createMainGraph(deps: AgentDependencies): DeployableGraph
export function createChatGraph(deps: AgentDependencies): DeployableGraph
```

Implementation areas:

- model/provider construction, fallback and effort resolution;
- prompt assembly and repository/user/team instructions;
- per-run preparation and dynamic context;
- middleware for message repair, input sanitation, timeouts, fallbacks, tool errors, message queue
  checks, plan mode, subdirectory instructions, sandbox circuit breaking, workflow push guards, PR
  creation guards, and completion behavior;
- curated tool registry and dynamic tool loading;
- subagent definitions, task retry, skills, and memory;
- input normalization, multimodal content, completion, title generation, and session cost.

Tools are grouped by capability inside the package (`planning`, `threads`, `workspace`, `github`,
`channels`, `web`, `settings`) rather than one source file per tool. Tool names and Zod input
schemas remain stable.

### `@open-swe/review`

This module owns the reviewer graph, analyzer graph, review chat, findings, diff grouping, style
learning, publishing, outcomes, reconciliation, and evaluation records.

Interface:

```ts
export function createReviewerGraph(deps: ReviewDependencies): DeployableGraph
export function createAnalyzerGraph(deps: ReviewDependencies): DeployableGraph
export interface Reviews {
  get(ref: PullRequestRef): Promise<ReviewRecord>
  request(request: ReviewRequest): Promise<DispatchedRun>
  chat(request: ReviewChatRequest): Promise<ReviewChatSession>
  reconcile(request: ReconcileReviewsRequest): Promise<ReconcileResult>
}
```

Findings use a repository owned by this module. The reviewer stays read-only except through its
publishing interface. Reviewer sandbox replacement remains explicitly allowed while main-agent
workspace replacement remains protected.

Analyzer skills remain Markdown assets loaded through the Deep Agents composite/state backend;
they are not copied into sandbox files.

### `@open-swe/automation`

This module owns deterministic scheduled work: user schedules, review-analysis cron registration,
CI watches, fallback polling, reconciliation, background command execution, and terminal
notifications.

Interface:

```ts
export function createSchedulerGraph(deps: AutomationDependencies): DeployableGraph
export interface Automations {
  create(input: ScheduleInput): Promise<Schedule>
  update(id: ScheduleId, input: SchedulePatch): Promise<Schedule>
  trigger(id: ScheduleId): Promise<DispatchedRun>
  remove(id: ScheduleId): Promise<void>
  evaluateWatch(input: WatchEvent): Promise<WatchResult>
  reconcile(): Promise<ReconcileResult>
}
```

The graph is only the deployment entrypoint. Deterministic scheduling and watch logic remain
ordinary functions behind `Automations`, so most tests do not invoke a model or compile a graph.

### `@open-swe/control-plane`

This module is the use-case layer consumed by TanStack Start's server functions and server routes.
It owns authorization and combines the lower modules for profiles, settings, repositories,
environments, snapshots, skills, plans, threads, diffs, reviews, usage, credentials, and admin
operations.

Interface:

```ts
export interface ControlPlane {
  sessions: SessionUseCases
  settings: SettingsUseCases
  repositories: RepositoryUseCases
  threads: ThreadUseCases
  reviews: ReviewUseCases
  automations: AutomationUseCases
  administration: AdministrationUseCases
}

export function createControlPlane(deps: ControlPlaneDependencies): ControlPlane
```

TanStack handlers only parse a request, call one use case, and serialize a schema-validated
response. Authorization lives here instead of being duplicated in route handlers. The large thread
and dashboard route files are split internally by use case, not exposed as separate workspace
packages.

### `@open-swe/test-support`

Private package containing builders and real adapters suitable for local substitution:

- in-memory `Runtime`, record store, credential vault, and channel clients;
- local workspace adapter and deterministic command results;
- signed GitHub, Slack, and Linear webhook fixture builders;
- fake chat models and scripted model responses;
- frozen clock, ID source, HTTP recorder, and route test client;
- parity fixtures captured from the current runtime.

Production packages never import this package.

### `ui` TanStack Start server

Owns:

- the sole public application origin and server entry;
- dependency construction for authentication and control-plane use cases;
- SSR, route loaders, request context, sessions, CSRF, logging, and error handling;
- server functions for typed dashboard-only reads and mutations;
- server routes for health, completion callbacks, webhooks, OAuth, SSE, WebSocket upgrades, binary
  downloads, and REST compatibility;
- an internal graph-runtime adapter using `@langchain/langgraph-sdk`;
- production and development forwarding for graph protocol routes that browser SDKs must reach.

The browser never receives internal graph-runtime credentials or an internal graph origin. The
existing backend proxy is deleted after its callers move to server functions/routes or to the
authenticated graph-runtime adapter.

### `apps/graphs`

Owns:

- dependency construction for graph execution;
- exports named `mainGraph`, `reviewerGraph`, `analyzerGraph`, `chatGraph`, and `schedulerGraph`;
- the only `langgraph.json` graph entry paths;
- no custom application routes, OAuth callbacks, dashboard handlers, or webhook handlers.

The graph IDs remain `agent`, `reviewer`, `analyzer`, `chat`, and `scheduler` for stored thread and
client compatibility.

The supported production shape keeps the graph server behind the TanStack server. The local graph
runtime exposes a full server that owns its listener, while `@langchain/langgraph-api` also exposes
an experimental embeddable router with only a subset of platform routes. Do not make production
correctness depend on that experimental interface. A future stable full-router export could remove
the internal network hop without changing application modules.

### `apps/ops`

Owns typed CLI commands replacing the current Python scripts:

- check pull-request merge state;
- purge wakeup crons;
- create and list sandbox snapshots;
- reconciliation and one-off repair commands.

Commands call package interfaces and never import server route files.

## Source ownership map

| Current area | Target owner |
|---|---|
| `agent/server.py`, `agent/chat.py`, `agent/prompt.py`, `agent/input_messages.py`, `agent/completion.py`, `agent/thread_title.py`, `agent/session_cost.py` | `packages/agent` |
| `agent/middleware/`, most of `agent/tools/` | `packages/agent` grouped by capability |
| `agent/runtime/`, LangGraph client/store helpers, `agent/dispatch.py` | `packages/runtime` |
| sandbox provider integrations, sandbox state, repo preparation, checkpoints, diffs | `packages/workspace` |
| GitHub, Slack, Linear, Notion, MCP, browser, and generic HTTP integrations | `packages/integrations` |
| OAuth, token resolution, encryption, credentials, identities, mappings | `packages/auth` |
| `agent/reviewer.py`, `agent/analyzer.py`, `agent/review/`, review tools | `packages/review` |
| `agent/scheduler.py`, `agent/baby_sit.py`, `agent/reconcile.py`, background tasks | `packages/automation` |
| `agent/dashboard/` use-case logic | `packages/control-plane` |
| `agent/api/`, `agent/webhooks/*_routes.py`, dashboard route declarations, `agent/webapp.py` | TanStack server functions/routes under `ui` |
| `agent/graphs/` | `apps/graphs/src/index.ts` exports |
| `scripts/*.py`, backend-only desktop launch/build helpers | `apps/ops` or TypeScript desktop scripts |
| `tests/**/*.py` | colocated `*.test.ts`, package integration tests, and TypeScript E2E fixtures |

## Cross-cutting conventions

### Configuration

Each package exports a Zod configuration fragment without reading `process.env`. The TanStack
server and graph composition root each merge only the fragments they own, parse the environment
once, and inject immutable configuration. Tests pass plain objects. Unknown environment variables
are tolerated; invalid known values fail at startup.

### Errors

Use a discriminated `AppError` union with stable codes and optional causes. External adapters map
SDK/HTTP failures once. TanStack request middleware maps known errors to HTTP responses once. Tool
wrappers map the same errors to tool messages once. Do not inspect exception strings at call sites.

### Time, IDs, and caches

Inject a clock only into modules that use deadlines or TTLs. Keep UUID generation behind a tiny
`IdSource` used by deterministic tests. Caches are owned by the module whose invariant they
protect; there is no global cache package.

### Logging and tracing

Pass one structured logger and tracing facade from the composition root. Every inbound request and
run carries correlation fields for thread, run, source, repository, and pull request when known.
Sensitive values are redacted at the facade.

### HTTP and streaming

Use web-standard `Request`, `Response`, `ReadableStream`, and `fetch` internally. TanStack server
routes handle external HTTP and raw streaming; server functions handle typed calls originating in
the app. Vendor clients may wrap `fetch`, but callers receive domain results rather than raw
responses. The graph-runtime adapter is the only module that knows its internal origin.

### Background work

Do not translate framework-managed background tasks into untracked promises. Webhook routes first
verify and normalize the event, then durably enqueue work through the runtime, scheduler graph, or
an owned queue before acknowledging. Short bounded work may be awaited. In-process parallel work
that belongs to an active graph run is awaited or retained by an owner with explicit shutdown and
error handling.

### Store compatibility

Create a registry of every existing namespace and record schema before porting writes. Readers
accept the current unversioned shape and the new versioned shape. Writers continue emitting a
backward-compatible shape until the TypeScript deployment has completed one full retention window.

### Test layout

- Pure behavior tests are colocated as `src/**/*.test.ts`.
- Adapter contract tests live as `src/**/*.contract.test.ts` and run against production and
  in-memory adapters.
- Package integration tests live under `packages/*/test/`.
- HTTP contract tests invoke the TanStack server fetch handler with web-standard requests.
- Graph tests use scripted chat models and in-memory runtime/workspace adapters.
- Playwright remains under `tests/e2e`, with all fixtures and harness code in TypeScript.

## Migration sequence and gates

### 0. Freeze compatibility and prove the desktop path

Capture the complete route inventory, tool registry, middleware stacks per graph, graph IDs, store
namespace registry, thread metadata registry, environment variables, webhook outcomes, and
representative serialized graph state. This freezes the eventual cloud-port contract without
making cloud parity a prerequisite for useful TypeScript software.

Run the desktop-blocking spikes first:

1. Prove TanStack can be the desktop application's single HTTP origin while forwarding graph SSE
   and WebSocket traffic to an internal JavaScript graph runtime with authentication, reconnect,
   cancellation, backpressure, and disconnect behavior intact.
2. Prove JavaScript discovery of the coding `createDeepAgent` graph, local bearer authentication,
   thread persistence, stream modes, and event streaming.
3. Prove `LocalShellBackend` confinement to an allowlisted project, a sanitized child environment,
   and artifact routing outside the repository.
4. Prove one production model path in JavaScript. OpenAI API-key authentication is the acceptance
   baseline; retain the existing OpenAI sign-in only if its JavaScript transport passes the same
   streaming, reasoning, cancellation, and output-normalization checks.
5. Prove the interactive terminal and local run stream through the packaged TanStack origin.
6. Prove that the packaged application can launch a bundled Node server and JavaScript graph
   runtime without Python, `uv`, or a source checkout.

Run the remaining provider, cryptography, durable-run, webhook, and hosted-sandbox spikes immediately
before the phase that consumes them. Every spike must leave an executable proof or recorded product
deviation. A failed spike changes its dependent phase rather than blocking the desktop milestone.

### 1. Establish the TypeScript workspace foundation

Add shared strict `tsconfig` files, formatter/linter configuration, Vitest projects, package export
rules, Node 24.18.1 engines, root pnpm scripts, and the package skeleton. Add `deepagents` and matching
LangChain package versions. Add a TanStack server entry and one minimal health server route. Wire
placeholder graph exports from `apps/graphs` in a migration-only config.

Gate: build, typecheck, lint, unit test, and local graph discovery all run through pnpm.

### 2. Port the local desktop boundary

Port only the contracts needed by local threads, local bearer auth, the project allowlist,
environment filtering, artifact routing, local thread persistence, and the local workspace adapter.
Move desktop platform behavior behind typed Electron IPC. Electron retains window, file-picker,
secure-storage, process-lifecycle, and PTY responsibilities; it does not define a second set of HTTP
application routes.

Gate: attempts to escape an allowlisted project fail; sensitive parent environment variables are
not inherited; artifacts cannot modify the repository; local thread records survive restart; all
desktop backend source is TypeScript.

### 3. Port a useful coding agent

Export one `createDeepAgent` graph for local coding. Support the built-in Deep Agents file and shell
capabilities, one production model path, model streaming, cancellation, and the existing checkpoint
and diff experience. Keep the graph factory and workspace interfaces reusable by the hosted port,
but do not add cloud-shaped abstractions that the local implementation does not exercise.

Gate: a scripted-model test opens a fixture repository, edits a file, runs its test command, and
returns a streamed result; cancellation terminates the run; the resulting diff matches the actual
worktree rather than reconstructed tool calls.

### 4. Ship the TypeScript-only desktop milestone

Run the TanStack server as the desktop application's sole HTTP origin and keep the JavaScript graph
runtime on a private loopback origin. Convert the remaining authored `.cjs` desktop modules and test
fixtures to TypeScript. Replace the Python runtime bundler and supervisor targets with a bundled
Node application, then update desktop development, packaging, CI, and documentation commands to use
pnpm only.

Gate: a packaged desktop build can add a local project, start a thread, stream an agent run, edit
files, execute tests, cancel a run, show the git diff, open a terminal, and restore the thread after
restart. The packaged resources contain no Python runtime, Python source, `uv`, or Python command.

### 5. Port hosted contracts, runtime, auth, and workspace providers

Port the remaining schemas and pure functions, then the SDK-backed runtime, store repositories,
dispatch, credential vault, encryption, sessions, OAuth, identity resolution, hosted workspace
lifecycle, reconnect/create behavior, metadata persistence, proxy refresh, git identity, checkout
preparation, snapshots, recovery patches, and downloads.

Gate: record fixtures round-trip; deterministic thread IDs match; cryptographic fixtures remain
readable; the production and local workspace adapters pass their shared contract suite; unreachable
main workspaces are never replaced; reviewer replacement is opt-in; existing metadata reconnects.

### 6. Port integrations and the hosted main/chat behavior

Port signed webhook parsing and outbound clients for GitHub, Slack, and Linear, followed by Notion,
MCP, observability, browser, and generic web tools. Normalize events before dispatch. Add the hosted
middleware and grouped tools to the desktop-proven graph module, preserving order-sensitive
behavior. Port each graph's actual middleware stack from the factory source rather than a single
global inventory. The currently unwired `ensure_no_empty_msg` and
`SandboxCircuitBreakerMiddleware` classes are not added merely because they exist; preserve the
sandbox notification/circuit helpers that wired code calls. Rewrite the sandbox-side
`background_execute` runner as POSIX shell so the backend does not upload a Python runtime helper.

Gate: captured webhook payloads preserve routing and outcomes; invalid signatures and unsafe URLs
are rejected; tool name/schema snapshots match; scripted-model graph tests cover plan mode, queued
messages, timeouts, fallback, task retry, sandbox failures, workflow guards, PR creation,
multimodal input, and completion.

### 7. Port review and analysis

Port findings, diff parsing/grouping, reviewer preparation, publishing, review chat, style learning,
outcomes, evaluations, and analyzer skills.

Gate: historical review fixtures produce equivalent findings and publishing decisions; reviewer
read-only rules and replacement behavior are enforced; analyzer records remain compatible.

### 8. Port automation

Port scheduler graph, user schedules, watches, webhook-triggered evaluations, polling fallback,
deduplication, rerun limits, reconciliation, and background execution.

Gate: fake-clock tests cover every transition and prove unchanged state does not invoke a model;
cron records created by the old runtime remain actionable.

### 9. Complete the hosted control plane, operations, evaluations, and E2E harness

Port the remaining dashboard use cases and authorization, then bind every compatibility route in
TanStack Start. Use server functions for app-only operations and server routes where an external
caller, raw response, or compatibility URL requires them. Keep URL, method, cookie, status,
response, streaming, CORS, and ownership behavior compatible. Replace operational scripts,
reviewer evaluation programs, and E2E harness files with TypeScript commands. Keep existing
JSON/TOML evaluation data where it is language-neutral. Update Docker, Make, CI, and documentation.

Gate: route inventory matches; shared Zod response schemas validate every successful fixture;
authorization matrix, CSRF, OAuth redirects, streaming, terminal, and diff downloads pass;
reviewer evaluations run through the JavaScript LangSmith client; operator commands support dry-run
where mutation is material.

### 10. Atomic cutover and deletion

Switch `langgraph.json` to `apps/graphs` and make TanStack the only public application origin.
Switch desktop launch paths to the TanStack server plus the local graph runtime; remove `agent/`,
all `.py` files, Python tests, `pyproject.toml`, `uv.lock`, virtual-environment setup, Python Docker
layers, and Python CI jobs. Regenerate lockfiles and documentation from their source inputs.

Gate:

```sh
test -z "$(find . -type f -name '*.py' -not -path './node_modules/*')"
test ! -e pyproject.toml
test ! -e uv.lock
pnpm install --frozen-lockfile
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm test:e2e
pnpm build
```

Run the full webhook-to-run, dashboard-to-run, review, scheduler, local sandbox, and desktop smoke
matrix before deployment.

## First implementation goal: a useful TypeScript-only desktop app

The first deliverable is not backend parity. It is a deliberately smaller edition of the existing
desktop product whose **This Mac** path contains no Python at development time or in the packaged
application.

The supported journey is:

1. Launch the desktop app and add an allowlisted local repository.
2. Create or reopen a local thread and choose the baseline supported model.
3. Ask the agent to inspect and edit files and run project commands through `LocalShellBackend`.
4. See tokens and tool activity stream, cancel a running turn, and send a follow-up turn.
5. Inspect the real git diff, use the existing terminal, and reopen the thread after restarting the
   app.

The implementation includes only the modules that make that journey real: strict shared contracts,
the local workspace adapter, the coding agent graph, thin `apps/graphs` composition, the TanStack
local-mode server bindings, and typed Electron platform adapters. The TanStack server is the only
authored HTTP backend. The graph server is an internal execution process, and Electron IPC is the
platform boundary rather than another application backend.

The following are explicitly out of scope for this milestone:

- hosted webhooks, OAuth sessions, GitHub App behavior, pull-request automation, and remote
  sandboxes;
- Slack, Linear, Notion, MCP, browser automation, schedules, watches, and durable cloud jobs;
- reviewer, review-chat, analyzer, and scheduler graphs;
- provider parity, fallback routing, team/profile settings, plan mode, subagents, and the complete
  production tool/middleware inventory;
- Windows/Linux packaging parity if the initial release is validated only on the current macOS
  packaging target.

OpenAI API-key authentication is the minimum model credential path. Existing OpenAI sign-in can be
included only if it does not delay the no-Python desktop gate. Missing features should be hidden or
shown as unavailable in local mode rather than routed back to the Python implementation.

Acceptance commands and checks must establish that desktop development and packaging invoke pnpm
and Node only, the authored desktop sources and tests are TypeScript, and packaged resources contain
no Python executable or module. One Playwright/Electron scenario must exercise the entire supported
journey against a temporary git repository.

After this milestone, reuse its contracts, graph module, workspace interface, TanStack bindings,
and packaging path for the hosted migration. Do not create placeholder ports for future vendors:
add each adapter seam when its production adapter and test adapter are implemented together.

## Completion definition

The port is complete only when:

- all five graph IDs run from TypeScript and every public application route is served by TanStack;
- every production tool is registered with compatible names and schemas;
- old threads, store records, schedules, review records, and sandbox IDs continue to work;
- webhook signatures, deterministic routing, OAuth, credentials, and authorization retain their
  security properties;
- deployment, local development, desktop packaging, operations, tests, and CI use pnpm/Node only;
- the repository contains no Python source or Python build/runtime metadata.
