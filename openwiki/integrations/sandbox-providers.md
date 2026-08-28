---
type: integration reference
title: Sandbox Provider Integrations
description: Reference for the pluggable sandbox providers Open SWE can run agents in, how the SANDBOX_TYPE selector and SANDBOX_FACTORIES registry choose a provider, the env vars each provider needs, and how to add a new provider.
tags: [sandbox, integrations, providers, langsmith, configuration, extension-point]
verified:
  - by: openwiki/0.4.2
    at: 2026-08-27T06:27:22.313Z
sources:
  - id: openwiki-source-8d388b16e97aa84ceab02561
    resource: repo://agent/integrations/daytona.py
  - id: openwiki-source-ad627c0857d0b3912124ca47
    resource: repo://agent/integrations/e2b.py
  - id: openwiki-source-06c03a92563e32b1726c4a22
    resource: repo://agent/integrations/langsmith.py
  - id: openwiki-source-5f57f8e958e980f50a83f09b
    resource: repo://agent/integrations/local.py
  - id: openwiki-source-6872956f9c811b444d08fdf1
    resource: repo://agent/integrations/modal.py
  - id: openwiki-source-118c6d2c33cb4ec0c0731444
    resource: repo://agent/integrations/runloop.py
  - id: openwiki-source-856ade03ef31ac38e1347f7c
    resource: repo://agent/server.py
  - id: openwiki-source-9393f5c0c83356ac7031b652
    resource: repo://agent/utils/sandbox.py
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-8010c6e64af5a375d8d3b70b
    resource: repo://docs/CUSTOMIZATION.md
generated: { by: "openwiki/0.4.2", at: "2026-08-27T06:27:22.313Z" }
---

# Sandbox Provider Integrations

Open SWE runs every agent task inside a **sandbox** — an environment that
implements `deepagents`' `SandboxBackendProtocol` and exposes file operations,
shell execution, and a stable `id`. The sandbox is where the agent clones the
repository, edits files, and runs commands. Which sandbox implementation backs a
run is pluggable: it is chosen at runtime from a small registry keyed by the
`SANDBOX_TYPE` environment variable.

This page documents the provider registry and selector, the individual provider
integration modules under `agent/integrations/`, the LangSmith default
provider's snapshot and GitHub-proxy specifics, and the recipe for adding a new
provider. For how a sandbox is bound to a thread, refreshed, and reclaimed over a
run's lifetime, see [architecture/sandbox-lifecycle](../architecture/sandbox-lifecycle.md).
For the full env-var surface, see [operations/configuration](../operations/configuration.md).

## The selector and the factory registry

The single entrypoint callers use is `create_sandbox()` in
`agent/utils/sandbox.py`. It reads `SANDBOX_TYPE` from the environment
(defaulting to `langsmith`), looks the provider up in `SANDBOX_FACTORIES`, and
invokes the matching factory.

`SANDBOX_FACTORIES` maps each provider name to a `(module, function)` tuple that
is imported lazily. This lazy import matters: only the selected provider's
module — and therefore only that provider's SDK dependency — is imported at
runtime, so a deployment that runs `modal` never needs the `langsmith` SDK
loaded and vice versa. An unknown `SANDBOX_TYPE` raises `ValueError` listing the
supported names.

| `SANDBOX_TYPE` | Module | Factory | Required env vars | Optional env vars |
|---|---|---|---|---|
| `langsmith` (default) | `agent.integrations.langsmith` | `create_langsmith_sandbox` | `LANGSMITH_API_KEY` or `LANGSMITH_API_KEY_PROD` | `SANDBOX_LANGSMITH_API_KEY`, `SANDBOX_LANGSMITH_ENDPOINT`, `DEFAULT_SANDBOX_SNAPSHOT_ID`, `DEFAULT_SANDBOX_*` resource vars, `SANDBOX_CREATE_EXTRA_JSON` |
| `daytona` | `agent.integrations.daytona` | `create_daytona_sandbox` | `DAYTONA_API_KEY` | `DAYTONA_SANDBOX_SNAPSHOT` |
| `modal` | `agent.integrations.modal` | `create_modal_sandbox` | Modal credentials (SDK-resolved) | `MODAL_APP_NAME` |
| `runloop` | `agent.integrations.runloop` | `create_runloop_sandbox` | `RUNLOOP_API_KEY` | — |
| `e2b` | `agent.integrations.e2b` | `create_e2b_sandbox` | `E2B_API_KEY` | `E2B_TEMPLATE` |
| `local` | `agent.integrations.local` | `create_local_sandbox` | none (no isolation — dev only) | `LOCAL_SANDBOX_ROOT_DIR` |

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
    Caller["create_sandbox(sandbox_id, ...)"] --> Type{"read SANDBOX_TYPE"}
    Type -->|langsmith| LS["create_langsmith_sandbox<br/>forward snapshot and resource options"]
    Type -->|daytona / e2b / runloop| Sync["run factory on asyncio.to_thread"]
    Type -->|modal| Async["await coroutine factory"]
    Type -->|local| Local["run LocalShellBackend setup on to_thread"]
    Type -->|unknown| Err["raise ValueError with supported names"]
    LS --> Backend["SandboxBackendProtocol"]
    Sync --> Backend
    Async --> Backend
    Local --> Backend
```
Provider selection and invocation inside `create_sandbox()`.

### Sync vs async dispatch

`create_sandbox()` calls each factory according to how that factory
provisions:

- The **langsmith** factory receives the extra keyword options
  (`snapshot_id`, `mem_bytes`, `vcpus`, `fs_capacity_bytes`, `create_params`) —
  filtered to those that are not `None` — because it is the only provider that
  honors them.
- **modal** is a coroutine function and is awaited directly, since its SDK
  provisions natively async.
- **daytona**, **e2b**, **runloop**, and **local** are synchronous callables
  run via `asyncio.to_thread`. daytona/e2b/runloop stay there because their
  `langchain_*` wrappers bind synchronous SDK handles; local stays there because
  `LocalShellBackend` setup performs synchronous filesystem I/O.

### Startup validation

`validate_sandbox_startup_config()` is called from the FastAPI lifespan hook so
misconfiguration surfaces at boot rather than on the first sandbox creation. It
only performs real validation for `langsmith` (delegating to
`LangSmithProvider.validate_startup_config()`); other providers validate their
env vars lazily inside their factory on first use.

### The SandboxGoneError invariant

`create_sandbox()` and the LangSmith reconnect path distinguish a sandbox that
is *merely unreachable* from one that no longer exists. A deleted sandbox holds
no working tree, so reconnecting to a missing id raises `SandboxGoneError`
(rather than a generic error), which tells callers to recreate instead of
failing the run. This is the reason there is intentionally **no delete** on the
provider interface: a sandbox is the agent's only copy of its working tree, so
reclamation is left to the platform via the create-time idle TTL and
delete-after-stop settings.

## Provider modules

Each provider lives in its own module under `agent/integrations/` and exposes a
single factory with the shape `create_<name>_sandbox(sandbox_id: str | None =
None)`: given an existing sandbox id it reconnects, otherwise it creates a fresh
sandbox, and it returns a `SandboxBackendProtocol`.

### LangSmith (default)

The default provider is the richest. `create_langsmith_sandbox` resolves the
sandbox snapshot configuration, delegates creation/reconnection to
`LangSmithProvider.get_or_create`, and — only for **newly created** sandboxes —
configures the GitHub proxy.

Key behaviors:

- **Credentials.** Sandbox operations use `SANDBOX_LANGSMITH_API_KEY` if set,
  otherwise the standard `LANGSMITH_API_KEY` / `LANGSMITH_API_KEY_PROD`. This
  lets sandboxes run against a different LangSmith workspace than the one used
  for tracing. The endpoint follows the same fallback via
  `SANDBOX_LANGSMITH_ENDPOINT` then `LANGSMITH_ENDPOINT`, defaulting to
  `https://api.smith.langchain.com`.
- **Snapshot config.** A new sandbox boots from `DEFAULT_SANDBOX_SNAPSHOT_ID`
  (overridable per-call), with resource limits from `DEFAULT_SANDBOX_VCPUS`,
  `DEFAULT_SANDBOX_MEM_BYTES`, and `DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES`,
  plus lifetime controls `DEFAULT_SANDBOX_IDLE_TTL_SECONDS` (default 2 h) and
  `DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS` (default 30 d). When no snapshot is
  configured, creation raises `ValueError` because there is no base image to
  boot from.
- **Create-body passthrough.** `SANDBOX_CREATE_EXTRA_JSON` (and per-call
  `create_params`) are merged into the create request; because the SDK's
  `create_sandbox` builds a fixed payload, the module wraps the HTTP client's
  `post` so the extra fields are injected only on the `POST /boxes` request.
- **Retries.** Sandbox creation retries a bounded number of times on retryable
  HTTP status codes and transient SDK errors with backoff.
- **Async-to-sync bridge.** Provisioning runs natively async through
  `AsyncSandboxClient`; the resulting `AsyncSandbox` is converted with
  `to_sync()` and wrapped in `TimeoutLangSmithSandbox` so it satisfies the sync
  `SandboxBackendProtocol` the agent's tools expect.
- **Execution deadline.** `TimeoutLangSmithSandbox` drives a non-blocking command
  handle and enforces a client-side deadline (the command's own timeout plus
  `SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS`); if the dataplane never emits an
  exit/error frame it kills the command and returns a timed-out result instead
  of wedging the run. WebSocket connect/setup failures fall back to the base
  HTTP execute path.

### The GitHub proxy — LangSmith only

Only the LangSmith provider performs the GitHub-proxy step. For a newly created
LangSmith sandbox, Open SWE calls `_configure_github_proxy`, which uses the
LangSmith proxy-config API to install header-injection rules so git and `gh`
traffic authenticates *on the wire* rather than by writing credentials to disk
in the sandbox:

- `github.com` / `*.github.com` get **Basic** auth (base64 of
  `x-access-token:<token>`) for git-over-HTTPS clone/pull/push.
- `api.github.com` gets **Bearer** auth for the REST API and `gh`; a
  placeholder `GH_TOKEN` env var is also set because `gh` refuses to run without
  a token in its environment even though the proxy injects the real one.

If the target sandbox is not `ready`, the proxy-config PATCH is rejected; the
module starts the sandbox best-effort and retries. The GitHub token itself is
minted at runtime from GitHub App installation credentials by the caller in
`agent/server.py` (`_create_sandbox_with_proxy`), not stored as a deployment
secret. A Stagehand model-key rule may also be injected when configured.

All **other providers (daytona, modal, runloop, e2b, local) skip the proxy
step** entirely — they neither accept a GitHub token nor rewrite outbound
traffic, so credentials for those setups are handled by the sandbox image or the
host environment.

### Daytona

`create_daytona_sandbox` requires `DAYTONA_API_KEY`, then either reconnects to an
existing sandbox by id or creates one from a snapshot. The snapshot defaults to
`daytonaio/sandbox:0.6.0` and is overridable via `DAYTONA_SANDBOX_SNAPSHOT`
(which must not be empty). It returns a `langchain_daytona.DaytonaSandbox`.

### Modal

`create_modal_sandbox` is async. Given an id it reattaches via
`modal.Sandbox.from_id`; otherwise it looks up the Modal app (name from
`MODAL_APP_NAME`, default `open-swe`) and creates a sandbox in it. It returns a
`langchain_modal.ModalSandbox`. Modal credentials are resolved by the Modal SDK
from its own configuration.

### Runloop

`create_runloop_sandbox` requires `RUNLOOP_API_KEY`, builds a Runloop client,
and either retrieves an existing devbox by id or creates a new one, returning a
`langchain_runloop.RunloopSandbox`.

### E2B

`create_e2b_sandbox` requires `E2B_API_KEY`. With an id it reconnects; otherwise
it creates a sandbox, optionally from a named template given by `E2B_TEMPLATE`
(which, if set, must not be empty). Sandboxes use a one-hour timeout by default.
It returns a `langchain_e2b.E2BSandbox`.

### Local (development only)

`create_local_sandbox` provides **no isolation** — it runs commands directly on
the host through `LocalShellBackend`, and is meant only for local development
with human-in-the-loop enabled. The `sandbox_id` argument is ignored (accepted
for interface compatibility). The root directory defaults to the current working
directory and is overridable via `LOCAL_SANDBOX_ROOT_DIR`. To avoid leaking
deployment secrets into shell commands, a set of provider API keys is stripped
from the inherited environment, and, so the run's bot git identity does not
overwrite the developer's real `~/.gitconfig`, `git config --global` is pointed
at a sandbox-local `.gitconfig-sandbox` file that `include`s the host's config.

## Adding a new provider

Adding a provider is a two-step change, per `docs/CUSTOMIZATION.md` and the
`AGENTS.md` conventions:

1. **Create a module** at `agent/integrations/<name>.py` with a factory
   matching the shared signature:

   ```python
   def create_my_provider_sandbox(sandbox_id: str | None = None):
       """Create or reconnect to a sandbox; return an object implementing SandboxBackendProtocol."""
       ...
   ```

   The factory reconnects when given an id and creates otherwise, and must
   return something implementing `SandboxBackendProtocol` from `deepagents`. The
   simplest way to get one is to extend `BaseSandbox` from
   `deepagents.backends.sandbox`, which implements all file operations in terms
   of `execute()`, so you only implement shell execution plus an `id` property.
   A factory may be sync or an `async def`; `create_sandbox()` dispatches on
   which it is.

2. **Register it** by adding an entry to `SANDBOX_FACTORIES` in
   `agent/utils/sandbox.py`:

   ```python
   SANDBOX_FACTORIES = {
       ...
       "my_provider": ("agent.integrations.my_provider", "create_my_provider_sandbox"),
   }
   ```

Selecting the new provider is then just `SANDBOX_TYPE=my_provider`. Non-LangSmith
providers inherit the no-proxy behavior automatically, and — since only
`langsmith` receives the snapshot/resource kwargs — a custom provider can ignore
those options unless it explicitly reads env vars of its own.
