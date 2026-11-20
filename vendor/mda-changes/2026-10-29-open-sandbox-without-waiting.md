# `aopen(wait=False)`: ask for the sandbox, wait for it on first use

**Verdict: upstream as-is.** A generic option on the run's managed sandbox.

**Changed:** `managed_deepagents/runtime.py` (`_LazyManagedSandboxBackend.aopen`, `_claim_managed_sandbox`, `_SandboxClaim`, `_claimed_sandboxes`, `_claim_langsmith_sandbox`, `_ready_sandbox`, `_create_langsmith_sandbox`, `_resolve_managed_sandbox`, `_adopt_sandbox`).

## What

- `runtime.backend.aopen(wait=False)` adopts the thread's sandbox or creates it with LangSmith's `wait_for_ready=False`, and returns its name without waiting for it to boot. The claim is kept per scope until the sandbox's first use. Robin calls it through `rt.managed_sandbox.OpenedSandbox.open`, which unwraps a `CompositeBackend`; the vendored `open_sandbox` helper is upstream's.
- The first use (`_resolve_managed_sandbox`) takes the claim, waits for the sandbox to be ready (`wait_for_sandbox`), and then runs the connectors' `sandbox` hooks, as a create does today. If the sandbox fails to boot, the claim is dropped and the next use asks for the sandbox again.
- `_adopt_sandbox` also adopts a sandbox that is still `provisioning`, and the first use waits for it. Before this change, a second process (or a run started right after a cancelled one) saw the name taken, could not adopt it, and created a second sandbox under a random suffix, stranding the first.

## Why

Booting a new sandbox takes about 11 s. Open SWE needs the sandbox's name before the run prepares, so the backend can bind the thread to it, but nothing in preparation runs in the sandbox. Waiting for the boot put those 11 s in front of preparation and the first model call.

## Upstream

Shipped: `sdks/managed-deepagents` (main and `mda/0.11`) has `ManagedSandboxBackend.aopen(wait=...)` and no `open_sandbox` helper, which is the call Robin makes. Drop this note when the vendored tree moves to that upstream. A stopped sandbox still restarts synchronously (`start_sandbox` waits for it to be ready); a `wait=False` start would need the SDK to accept one.
