# Task delegation

`configure_task` persists acceptance criteria and the coordinator's completion assessment. `spawn_worker` creates a normal durable agent thread; `message_task_thread` provides explicit noninterrupting messages and help requests; `control_worker` supports status, cancellation, and retrying an initial dispatch.

Postgres keeps task identity, criteria, and membership separate from delegation records. The first delegation permanently changes the coordinator to coordination-only. A Postgres advisory lock serializes native tool execution with that transition. Workers cannot delegate. The shared tool catalog is unchanged between roles; execution checks read persisted membership and each model call receives refreshed role restrictions after the shared prefix.

Worker events reuse the checkpoint-acknowledged event delivery infrastructure with stable completion IDs, idle wake-up, and busy-thread pickup. Task events are exempt from webhook retention. Worker invocation completion is not task completion. Delegation requires Postgres and a configured completion webhook. This initial implementation requires a user-owned coordinator.

Workers share the coordinator's sandbox. Concurrent edits, checkout operations, and tests require coordinator scheduling; independent checkpoints do not isolate mutable files. Runtime model, effort, and assignment instructions are supported. Per-worker tool permissions, skills, context forking, and sandbox selection are not implemented.

## Follow-up

Shared sandbox tool-proxy credentials currently identify the sandbox host, not the executing worker. Per the initial scope decision, proxy identity isolation is not implemented here. Spawning through the tool proxy is rejected, but other proxy calls still rely on its existing authentication. Native role enforcement is not a replacement for fixing that boundary.

Native coordinator tools use a fail-closed coordination allowlist after delegation. Worker restrictions reject built-in delegation and known external agent-launch tools; arbitrary future MCP launch tools still require classification. Processes already started in the sandbox are not terminated by the delegation transition. These are enforcement gaps to address before treating this as a hardened isolation boundary.

Task creation/dispatch spans Postgres and LangGraph. Reserved worker membership and the initial owed message survive failures; retry uses the same worker identity. Automatic reconciliation of undispatched reservations is a follow-up; the coordinator can use `task_status` to discover reservations even if a crash lost the tool response, then explicitly retry the worker ID. Retry resets the initial delivery attempt budget without replaying checkpointed assignments. Completion wake-up uses the existing completion webhook and bounded event delivery retries.
