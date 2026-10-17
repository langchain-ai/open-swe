---
type: integration reference
title: Sandbox Providers and External Execution
description: How Open SWE selects sandbox backends, separates built-in LangSmith behavior from local and optional third-party providers, and safely binds external execution environments to threads.
tags: [sandbox, integrations, providers, langsmith, configuration, execution]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-17T08:12:29.923Z
sources:
  - id: openwiki-source-4b1279a0a1e5ec2d55a4558a
    resource: repo://openswe/api/app.py
  - id: openwiki-source-b3a1e5fc7fe45f62e902bef9
    resource: repo://openswe/config.py
  - id: openwiki-source-96bcad07b4fe7078402bc2b8
    resource: repo://openswe/reviewer.py
  - id: openwiki-source-1b32e9f41fa7e64702b380f6
    resource: repo://openswe/sandboxes/lifecycle.py
  - id: openwiki-source-ac289677ed2c2c7d2436d024
    resource: repo://openswe/sandboxes/providers/daytona.py
  - id: openwiki-source-5d9c36f48ae657b2e73411eb
    resource: repo://openswe/sandboxes/providers/e2b.py
  - id: openwiki-source-d16a45e9fc6aa80a3708c88c
    resource: repo://openswe/sandboxes/providers/langsmith.py
  - id: openwiki-source-5b3f60be6fd7ddbdf61f37ad
    resource: repo://openswe/sandboxes/providers/local.py
  - id: openwiki-source-b00a933bb60c0ef13ec9e872
    resource: repo://openswe/sandboxes/providers/modal.py
  - id: openwiki-source-a4c632cb1c0a9a7a637ab9fe
    resource: repo://openswe/sandboxes/providers/registry.py
  - id: openwiki-source-c53320967c7601e515b66c5b
    resource: repo://openswe/sandboxes/providers/runloop.py
  - id: openwiki-source-c5766699cee46f671b69bf81
    resource: repo://openswe/sandboxes/repo_prep.py
  - id: openwiki-source-63c74145043dac21719006df
    resource: repo://openswe/sandboxes/retry.py
  - id: openwiki-source-7b35cf61ea1491240ef4c804
    resource: repo://openswe/workspaces/store.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-7c557728721b38cad5fe3518
    resource: repo://tests/sandbox/test_langsmith_sandbox_config.py
  - id: openwiki-source-130707758e1dbbca1a9205d1
    resource: repo://tests/sandbox/test_langsmith_sandbox_timeout.py
  - id: openwiki-source-68ad90e24a41215f464ec35a
    resource: repo://tests/sandbox/test_optional_provider_extras.py
  - id: openwiki-source-8df2adb4d3d3b703aed3451b
    resource: repo://tests/sandbox/test_sandbox_publish_ordering.py
  - id: openwiki-source-b074bf11145a0ff6206cec7b
    resource: repo://tests/sandbox/test_sandbox_retry.py
generated: { by: "openwiki/0.4.2", at: "2026-10-17T08:12:29.923Z" }
---

# Sandbox Providers and External Execution

Open SWE executes repository commands through `SandboxBackendProtocol`. Provider choice is deployment configuration, not an agent-graph decision: the registry constructs a backend, while the [sandbox lifecycle](../architecture/sandbox-lifecycle.md) owns thread metadata, initialization, and publishing. This page describes the provider boundary; see [configuration](../operations/configuration.md) for the full environment-variable reference and [deployment](../operations/deployment.md) for deployment concerns.

## Selection, dependencies, and startup

`SANDBOX_TYPE` defaults to `langsmith`. The registry lazily resolves one of `langsmith`, `daytona`, `modal`, `runloop`, `e2b`, or `local`; an invalid value raises a `ValueError` naming the supported choices. Lazy imports keep unused SDKs out of the normal process.

LangSmith and Local are base dependencies. Daytona, Modal, Runloop, and E2B are optional dependency groups in `pyproject.toml`: install one with `uv sync --extra sandbox-<provider>`, or all with `uv sync --extra sandbox-providers`. If the selected provider's own SDK is missing, the registry converts that `ModuleNotFoundError` into an install hint; unrelated import failures are allowed to propagate. Startup eagerly checks this dependency boundary for an optional selected provider, so a missing extra fails before the first sandbox request.

All factories accept `sandbox_id: str | None`: an id means reconnect and no id means create. `create_sandbox()` passes snapshot, sizing, and arbitrary create-body parameters only to LangSmith. It awaits native asynchronous LangSmith and Modal factories, and runs Daytona, Runloop, E2B, and Local factories in `asyncio.to_thread` so their synchronous SDK or filesystem setup does not block the event loop.

```mermaid
flowchart TD
    Need["Thread needs a backend"] --> Select["Read SANDBOX_TYPE"]
    Select --> Load["Lazy-load registered factory"]
    Load --> Optional{"Optional SDK available"}
    Optional -->|"no"| Hint["ValueError with install command"]
    Optional -->|"yes"| Create{"LangSmith"}
    Create -->|"yes"| Settings["Pass snapshot resources and create fields"]
    Create -->|"no"| Invoke["Pass sandbox id only"]
    Settings --> Backend["SandboxBackendProtocol"]
    Invoke --> Backend
    Backend --> Bind["Lifecycle initializes then persists and publishes"]
```
Provider selection and the boundary between provider construction and thread binding.

The FastAPI lifespan invokes `validate_sandbox_startup_config()` before serving. For LangSmith, it validates that configured capacity and TTL variables are integers, rejects negative TTLs, and checks that `SANDBOX_CREATE_EXTRA_JSON` is a JSON object. Other provider credentials are checked when their factory is called.

## Provider comparison

| Provider | Create or reconnect | Configuration and package boundary | Operational boundary |
|---|---|---|---|
| `langsmith` | Asynchronously gets an existing box or creates a new one, returning `TimeoutLangSmithSandbox`. | Base dependency; `LANGSMITH_API_KEY`, `LANGSMITH_ENDPOINT`, snapshot/resource/TTL settings, and create-body fields. | The only provider receiving registry snapshot/resource parameters; supports managed proxy configuration, workspace snapshots, stop-on-recreation, and workspace service URLs. |
| `local` | Builds a new `LocalShellBackend`; ignores ids. | Base dependency; optional `LOCAL_SANDBOX_ROOT_DIR` and `GIT_CONFIG_GLOBAL`. | Commands run on the host without isolation—local development with human oversight only. |
| `daytona` | Gets an id or creates from a Daytona snapshot. | `sandbox-daytona`; requires `DAYTONA_API_KEY`; `DAYTONA_SANDBOX_SNAPSHOT` defaults to `daytonaio/sandbox:0.6.0`. | Synchronous wrapper. |
| `modal` | Reattaches with `Sandbox.from_id.aio()` or creates in an app. | `sandbox-modal`; Modal authentication; `MODAL_APP_NAME` defaults to `open-swe`. | Native async wrapper. |
| `runloop` | Retrieves an existing devbox or creates one. | `sandbox-runloop`; requires `RUNLOOP_API_KEY`. | Synchronous wrapper. |
| `e2b` | Connects to an id or creates a sandbox, optionally from a template. | `sandbox-e2b`; requires `E2B_API_KEY`; optional `E2B_TEMPLATE`. | Uses a one-hour SDK timeout and a synchronous wrapper. |

The common factory shape makes basic creation portable, but it does **not** make advanced lifecycle capabilities portable. In particular, snapshots, proxy credential injection, and stopping an old sandbox during recreation are implemented against LangSmith. A recreation with another provider still creates and binds a distinct new backend, but reports that stopping the old provider backend is unsupported.

## Thread binding protects working state

A thread can have an in-memory connection and a persisted `sandbox_id`. `ensure_sandbox_for_thread()` reuses the cached backend when available, otherwise reconnects using that id, or creates a new backend when none is bound. A task worker is different: it verifies ownership, workspace, visibility, and repository scope against its coordinator and attaches to the coordinator's sandbox instead of creating or replacing one.

```mermaid
flowchart TD
    Metadata["Read thread metadata"] --> Worker{"Task worker"}
    Worker -->|"yes"| Attach["Validate coordinator and attach shared backend"]
    Worker -->|"no"| Existing{"sandbox_id present"}
    Existing -->|"no"| Boot["Resolve workspace snapshot and boot"]
    Existing -->|"yes"| Connect["Use cache or reconnect"]
    Connect --> Result{"Gone or unreachable"}
    Result -->|"gone"| Boot
    Result -->|"unreachable"| Fail["Raise SandboxUnreachableError"]
    Result -->|"unreachable and allowed"| Boot
    Boot --> Init["Set git identity and LangSmith proxy"]
    Init --> Persist["Persist new sandbox_id"]
    Persist --> Publish["Publish backend proxy"]
    Attach --> Publish
```
Thread recovery distinguishes deleted state from unreachable state before it changes a binding.

For LangSmith, `ResourceNotFoundError` reconnect failures become `SandboxGoneError`; a deleted backend is recreated because it has no remaining working tree. Other connection or proxy-refresh failures become `SandboxUnreachableError` and normally fail the run rather than silently replacing uncommitted work. Reviewer and review-scout callers opt into replacement because they regenerate their checkout on every run.

A new backend is initialized before its id is persisted, and is published to the in-memory proxy only after persistence, handoff, and tool URL provisioning. Thus a failed creation or metadata update does not leave later work adopting a half-initialized backend. Recreation similarly refuses a non-distinct id and keeps the old cached binding if metadata persistence fails. The provider abstraction deliberately has no delete operation: platform TTLs, rather than a metadata-driven delete, reclaim working-tree-bearing boxes.

## LangSmith provisioning, snapshots, and proxy behavior

LangSmith uses `LANGSMITH_API_KEY` and `LANGSMITH_ENDPOINT`; the provider turns an API root into the SDK sandbox base ending in `/v2/sandboxes`. New boxes omit `snapshot_id` when none is supplied, which selects the platform root snapshot. Default capacity is 4 vCPUs, 16 GiB memory, and 128 GiB filesystem; default idle and delete-after-stop TTLs are two hours and 30 days. A partial CPU or memory override deliberately leaves the other value unset rather than combining it with the default; `0` disables either TTL.

Workspace resolution supplies a ready workspace snapshot and workspace resource settings when available, otherwise a base/root snapshot. It also adds `preserve_memory_on_stop: true` to provider create parameters. Snapshot capture itself is LangSmith-specific: it captures under a mutable `name:latest` tag and only updates the workspace record after capture succeeds.

`SANDBOX_CREATE_EXTRA_JSON` provides deployment-wide extra create fields; call-specific `create_params` win on conflicts. Because the SDK lacks a public arbitrary-field seam, the integration temporarily wraps its HTTP client so extra fields are injected only into `POST .../boxes`, not subsequent requests. Retryable creation errors receive up to three attempts. This is an intentional SDK integration boundary worth revisiting when the client exposes a public payload API.

For each new or reused LangSmith thread backend, the lifecycle obtains a repository-scoped GitHub access token and configures the sandbox proxy. It injects Bearer authentication for `api.github.com` and Basic authentication for `github.com` and `*.github.com`; `GH_TOKEN` inside the sandbox is only the `proxy-injected` placeholder, not the real token. Custom proxy rules are preserved except retired managed rules, and a not-ready proxy update starts the stopped sandbox best-effort before retrying. Non-LangSmith providers do not receive this proxy refresh.

LangSmith can also expose a sandbox port as a workspace-authenticated service URL. The `expose_port` tool validates ports, requests that URL for the current backend, and rejects the result if the backend changed during the request.

## LangSmith execution semantics

`TimeoutLangSmithSandbox` is async-only. For commands with an effective timeout it opens a nonblocking command handle, waits for the command timeout plus `SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS` (30 seconds by default), and returns an exit-124 response on either server-side timeout or a client deadline. Client expiry best-effort kills and drains the handle; cancellation does the same. WebSocket connection, reload, and selected stream failures fall back to the base execution path. If command state is lost while collecting a result, it raises rather than replaying a command whose effects are unknown.

Automatic command retry is narrower still: only `SandboxRetryableConnectionError` qualifies, because it represents a rejected WebSocket upgrade before the execute frame was sent. Retries use jittered exponential backoff and default to four attempts, avoiding double execution of repository mutations.

## Local execution safety

The Local provider creates its root directory (defaulting to the process working directory) and runs a `LocalShellBackend` with `inherit_env=False`. It starts from the host environment but removes selected model and LangSmith credentials. Unless `GIT_CONFIG_GLOBAL` is explicitly configured, it writes `.gitconfig-sandbox` under the local root and points Git there; that file includes the host configuration so helpers and aliases remain available while Open SWE's bot identity does not overwrite `~/.gitconfig`.

This isolation is credential and Git-config hygiene, not a security sandbox. Local commands can affect the host filesystem and should not be used as an untrusted multi-user execution environment.

## Reviewer checkout exception

Reviewer sandboxes are reusable, but their repository contents are intentionally re-derived. `prepare_review_repo()` clone-or-fetches using a blobless clone, fetches the base and target head (and the pull ref for fork PRs), force-checks out the requested head, and verifies `HEAD`, with a 240-second command timeout. On failure it returns `False` so review can continue from fetched diff context rather than failing solely because checkout preparation failed.

Trusted skills are a separate safety boundary: `.agents/skills` and `.claude/skills` are extracted from the PR base reference into a sibling `.review-skills` path, never from the author-controlled PR head.

## Adding or operating a provider

To add a provider, implement `create_<name>_sandbox(sandbox_id: str | None = None)` that reconnects or creates and returns a `SandboxBackendProtocol`, then register its module and callable in `SANDBOX_FACTORIES`. Factories may be synchronous or asynchronous. If its SDK is optional, add a `sandbox-<name>` extra and teach the registry which missing SDK module should produce the operator-facing install hint.

Do not treat the factory contract as a promise of feature parity. Decide and test how the provider handles reconnect failures, git identity, workspace boot images, replacement, reset/recreation, proxy egress credentials, and cleanup. Preserve the key safety invariant: an unreachable sandbox with possible uncommitted work must not become an empty replacement by accident.

## Focused verification

- `tests/sandbox/test_optional_provider_extras.py` verifies dependency-group install hints and propagation of unrelated import errors.
- `tests/sandbox/test_langsmith_sandbox_config.py` covers create payload injection, retry, missing-box classification, snapshot tagging, and service URL requests.
- `tests/sandbox/test_langsmith_sandbox_timeout.py` covers deadline handling, kill-on-cancellation, fallback, unknown command outcome, file behavior, and retry safety.
- `tests/sandbox/test_sandbox_publish_ordering.py`, `test_sandbox_recovery.py`, and `test_sandbox_recreation.py` cover binding and handoff ordering, recovery policy, and preservation of the old binding on persistence failure.
