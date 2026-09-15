---
type: integration reference
title: Sandbox Provider Integration
description: How Open SWE selects, provisions, reconnects, and operates sandbox backends. Covers LangSmith-specific lifecycle capabilities, alternate providers, local and desktop execution, and environment snapshots.
tags: [sandbox, integrations, providers, langsmith, configuration, extension]
verified:
  - by: openwiki/0.4.2
    at: 2026-09-15T08:15:12.744Z
sources:
  - id: openwiki-source-328bde9e94017848bb09ba23
    resource: repo://agent/api/app.py
  - id: openwiki-source-b05c9910677cf23a9325276c
    resource: repo://agent/config.py
  - id: openwiki-source-e70a8aff497c71b755dfc906
    resource: repo://agent/environments/refresh.py
  - id: openwiki-source-178ecfdbca83725129c21856
    resource: repo://agent/environments/sandbox_settings.py
  - id: openwiki-source-a932abf8e3e085c1cce4772d
    resource: repo://agent/environments/store.py
  - id: openwiki-source-276ab38291eb5741b4c2141c
    resource: repo://agent/reviewer.py
  - id: openwiki-source-6fd11c8bb15f5eb94b765440
    resource: repo://agent/sandboxes/lifecycle.py
  - id: openwiki-source-92118671e3d396d6804d8f9c
    resource: repo://agent/sandboxes/providers/daytona.py
  - id: openwiki-source-de402a49ebddbc7dfd6e029a
    resource: repo://agent/sandboxes/providers/e2b.py
  - id: openwiki-source-2dedcea02c5aa03c54d81c32
    resource: repo://agent/sandboxes/providers/langsmith.py
  - id: openwiki-source-0746ff3f107493deffefb33b
    resource: repo://agent/sandboxes/providers/local.py
  - id: openwiki-source-0f48a3dcf38220dbcd5d9d0e
    resource: repo://agent/sandboxes/providers/modal.py
  - id: openwiki-source-49bfbb811c25e99235121924
    resource: repo://agent/sandboxes/providers/registry.py
  - id: openwiki-source-c9c9a42cf879f76a6fb780f9
    resource: repo://agent/sandboxes/providers/runloop.py
  - id: openwiki-source-d1484acd34e71448e75b9559
    resource: repo://agent/sandboxes/read_only_backend.py
  - id: openwiki-source-c2e0c61bef110853a29c63a8
    resource: repo://agent/sandboxes/repo_prep.py
  - id: openwiki-source-267a662990890ab782a8bf32
    resource: repo://agent/sandboxes/retry.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-8010c6e64af5a375d8d3b70b
    resource: repo://docs/CUSTOMIZATION.md
  - id: openwiki-source-7c557728721b38cad5fe3518
    resource: repo://tests/sandbox/test_langsmith_sandbox_config.py
  - id: openwiki-source-6c4c3340e6bc2f86a0e54411
    resource: repo://tests/sandbox/test_local_integration.py
generated: { by: "openwiki/0.4.2", at: "2026-09-15T08:15:12.744Z" }
---

# Sandbox Provider Integration

Open SWE performs repository work through `SandboxBackendProtocol`. Provider selection is deployment configuration, while the sandbox lifecycle owns the durable thread binding, reconnection policy, initialization, and publication ordering. See [sandbox lifecycle](../architecture/sandbox-lifecycle.md) for the broader thread lifecycle and [configuration](../operations/configuration.md) for deployment variables.

## Provider selection and startup checks

`SANDBOX_TYPE` defaults to `langsmith`. The registry lazily imports the selected registered factory—`langsmith`, `daytona`, `modal`, `runloop`, `e2b`, or `local`—so unused provider SDKs need not load. An unknown value raises `ValueError` and reports the supported types.

Each factory accepts `sandbox_id: str | None`: an id means reconnect and no id means create. `create_sandbox()` forwards snapshot, resource, and create-body options only to LangSmith. Native async factories are awaited; synchronous factories run in `asyncio.to_thread`, keeping their blocking SDK or filesystem setup off the event loop.

```mermaid
flowchart TD
    Need["Thread needs a sandbox"] --> Select["Read SANDBOX_TYPE"]
    Select --> Load["Lazy-load registered factory"]
    Load --> Known{"Provider known"}
    Known -->|"no"| Invalid["ValueError lists supported types"]
    Known -->|"langsmith"| LangSmith["Await with snapshot and resource options"]
    Known -->|"other async"| AsyncFactory["Await factory with id"]
    Known -->|"synchronous"| Worker["Run factory in worker thread"]
    LangSmith --> Backend["SandboxBackendProtocol"]
    AsyncFactory --> Backend
    Worker --> Backend
```
Provider selection and dispatch; only LangSmith receives selector-level creation options.

The FastAPI lifespan calls `validate_sandbox_startup_config()` before serving. Validation currently delegates only to LangSmith: configured resource and TTL settings must parse as integers, idle and delete-after-stop TTLs cannot be negative, and `SANDBOX_CREATE_EXTRA_JSON` must be a JSON object. Other providers validate required credentials when their factories run.

## Thread binding, initialization, and recovery

`ensure_sandbox_for_thread()` first finds an in-memory backend or the saved thread metadata id; otherwise it creates a box from the selected environment's ready snapshot and settings, falling back to the administrator base snapshot. It applies the bot Git identity on creation and reuse. A stale environment snapshot can also run its update script in the newly created sandbox before the first model call; script errors are logged but do not prevent use of the already-bootable image.

```mermaid
flowchart TD
    Start["Ensure sandbox for thread"] --> Cache{"Cached backend"}
    Cache -->|"yes"| Reuse["Refresh identity and proxy"]
    Cache -->|"no"| Metadata{"Metadata id"}
    Metadata -->|"no"| Create["Resolve environment and boot sandbox"]
    Metadata -->|"yes"| Connect["Reconnect by id"]
    Connect --> Result{"Connection result"}
    Result -->|"gone"| Create
    Result -->|"unreachable"| Policy{"Replacement allowed"}
    Policy -->|"yes"| Create
    Policy -->|"no"| Fail["Raise SandboxUnreachableError"]
    Create --> Init["Identity, LangSmith proxy, update script"]
    Init --> Persist["Persist new sandbox id"]
    Reuse --> Publish["Publish backend proxy"]
    Persist --> Publish
```
Provisioning and recovery preserve a working tree by distinguishing deletion from unreachability and publishing only an initialized backend.

A LangSmith `ResourceNotFoundError` becomes `SandboxGoneError`, which is recreated because it has no surviving working tree. Other reconnect failures become `SandboxUnreachableError` and normally stop the run rather than replacing potentially uncommitted work with an empty sandbox. `allow_replacement=True` makes that trade-off for review threads, whose checkout is re-derived for every review.

Creation is bound safely: the lifecycle persists a new `sandbox_id` only after initialization completes, then publishes the in-memory backend last. Reset and recreate require a distinct new id and likewise persist the handoff before replacing the cached backend. This avoids later work adopting a partly initialized backend.

The provider abstraction intentionally has no sandbox-delete operation. Because a sandbox can contain the agent's only working tree and metadata lookup can fail open to no sandbox, reclamation is delegated to platform idle and delete-after-stop TTLs.

## Providers and operational configuration

| Provider | Create or reconnect behavior | Required or notable configuration |
|---|---|---|
| `langsmith` | Async client gets an id or creates a box, wrapped as `TimeoutLangSmithSandbox` | `LANGSMITH_API_KEY`; `LANGSMITH_ENDPOINT`; snapshots, resources, TTLs, and extra create fields |
| `daytona` | Gets an id or creates from a snapshot | `DAYTONA_API_KEY`; `DAYTONA_SANDBOX_SNAPSHOT` defaults to `daytonaio/sandbox:0.6.0` |
| `modal` | Reattaches by id or creates in an app | Modal credentials; `MODAL_APP_NAME` defaults to `open-swe` |
| `runloop` | Retrieves an id or creates a devbox | `RUNLOOP_API_KEY` |
| `e2b` | Connects by id or creates a sandbox | `E2B_API_KEY`; optional `E2B_TEMPLATE`; one-hour timeout |
| `local` | Creates a host-backed `LocalShellBackend`; ignores ids | Optional `LOCAL_SANDBOX_ROOT_DIR`; no isolation |

### LangSmith provisioning and proxy service

LangSmith sandbox provisioning, connection, proxy configuration, and environment capture use the deployment's `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`. The SDK endpoint is normalized to `/v2/sandboxes`; the retired `SANDBOX_LANGSMITH_API_KEY` and `SANDBOX_LANGSMITH_ENDPOINT` overrides are not used.

When a snapshot is configured, new boxes boot from it; otherwise the create request omits `snapshot_id` so the platform selects its root snapshot. Default sizing is 4 vCPUs, 16 GiB memory, and 128 GiB filesystem capacity. Default idle and delete-after-stop TTLs are two hours and 30 days, respectively; zero disables either TTL. If only CPU or memory is overridden, the other is intentionally left unset rather than combined with its deployment default.

`SANDBOX_CREATE_EXTRA_JSON` contributes deployment-wide create fields, and call-specific `create_params` win on conflicts. Unsupported fields are injected only into the SDK `POST /boxes` request. Retryable creation failures get at most three attempts.

On LangSmith creation and reuse, the lifecycle mints a GitHub App installation token and configures proxy rules rather than writing the real token into the sandbox. The proxy applies Basic authentication for `github.com` and `*.github.com`, and Bearer authentication plus a placeholder `GH_TOKEN` for `api.github.com`. Caller-provided base rules are preserved; a not-ready proxy update starts the box best-effort and retries. Proxy refresh failures make the sandbox unreachable.

`TimeoutLangSmithSandbox` gives async command execution a client-side deadline around a nonblocking command. Server command timeout and client deadline exhaustion both return exit code 124; client expiration best-effort kills the command. Supported WebSocket setup and stream failures fall back to base execution. Command retry is deliberately restricted to `SandboxRetryableConnectionError`, which means the WebSocket upgrade failed before the execute frame was sent, and uses at most four jittered exponential-backoff attempts.

### Environment snapshots and reset

The environment system is LangSmith-specific. An environment record selects a ready snapshot plus resource and create settings; without a ready snapshot, creation falls back to the administrator base snapshot, then `DEFAULT_SANDBOX_SNAPSHOT_ID`. The administrator setting is store-backed so a base image can change without redeployment, and its lookup fails soft to the environment default.

Refresh uses a throwaway builder: a full refresh starts from the base snapshot and runs setup then update, while an update refresh starts from the current environment snapshot and runs only the update script. A successful capture publishes a mutable `name:latest` tag and records the immutable snapshot id used by new runs, so a mid-run refresh cannot change a reconnecting sandbox. Failed refreshes retain the last working snapshot; builders are stopped for TTL-based reclamation.

`sandbox_reset` is supported only for LangSmith. It creates a distinct replacement from raw create parameters, configures proxy and Git identity, then updates metadata before swapping the cached backend. Generic recreation uses the environment-derived configuration and preserves the prior sandbox until the same handoff succeeds.

### Local and desktop execution

`local` executes directly on the host and is for local development with human oversight, not isolation. It creates its root directory, passes a filtered environment with `inherit_env=False`, and excludes selected model, LangSmith, and OAuth secrets. Unless `GIT_CONFIG_GLOBAL` is explicitly set, it directs global Git writes to root-local `.gitconfig-sandbox`, which includes the host configuration and protects the developer's `~/.gitconfig` identity.

Desktop execution is not a provider selection. Its primary backend is the user project; it composes read-only bundled and state-backed user-skill routes plus separate artifact routes so agent scratch files do not enter the project. `ReadOnlyBackend` delegates only asynchronous reads, listing, grep, glob, and downloads, while synchronous calls are rejected.

## Reviewer preparation

Reviewer sandboxes are reusable but repo content is re-derived. Before the first model call, `prepare_review_repo()` clone-or-fetches the repository, fetches base and head—including the pull ref for forks—force-checks out the expected head SHA, and verifies `HEAD`. It uses a 240-second timeout and returns `False` on preparation failure, allowing review to continue from fetched diff context.

Trusted skills receive stricter treatment: `.agents/skills` and `.claude/skills` are extracted from the PR base SHA, not the author-controlled head, into sibling `.review-skills` paths. This prevents a pull request from injecting reviewer instructions through a changed `SKILL.md`.

## Adding a provider

1. Implement `agent/sandboxes/providers/<name>.py` with `create_<name>_sandbox(sandbox_id: str | None = None)`. Reconnect when passed an id, create otherwise, and return `SandboxBackendProtocol`. Factories may be synchronous or `async def`.
2. Add the corresponding `(module, function)` entry to `SANDBOX_FACTORIES`.
3. Define credentials and reconnect failure semantics. Do not silently replace an unreachable persistent working tree.
4. Test creation/reconnection and registry dispatch, and explicitly decide whether snapshots, reset, resource overrides, proxy refresh, and other LangSmith-specific capabilities are unsupported or need equivalents.

A custom backend can extend `deepagents.backends.sandbox.BaseSandbox`, delegating file operations to shell execution, but it must support the asynchronous operations used by the lifecycle.

## Focused verification

`tests/sandbox/test_langsmith_sandbox_config.py` covers endpoint construction, snapshot omission, defaults, validation, create-field injection, retries, and missing-box classification. `test_langsmith_sandbox_timeout.py` covers deadlines, kill behavior, response conversion, fallback, and command retry. Lifecycle tests cover recovery, reset/recreate handoff ordering, proxy behavior, environment update scripts, and reviewer replacement policy; Local tests cover root, filtered environment, and Git-config isolation.
