# Preserve a thread's sandbox when adoption fails

**Verdict: not upstream as-is.** Preserving work is right; selecting this policy with `_openswe_per_thread` is Open SWE-specific.

**Changed:** `managed_deepagents/runtime.py` (sandbox lookup, restart, adoption, resolution and tool-error recovery).

## What

With `_openswe_per_thread=True`, only a sandbox lookup that reports not-found permits creation. Other lookup errors, failed restarts and unusable existing sandboxes fail the open instead. A create-name conflict may adopt the concurrent winner, but never creates a second sandbox with a random suffix. A tool reporting that the cached sandbox is gone does not implicitly replace its workspace. Explicit `replace_sandbox` remains available and retains the thread's original sandbox name.

## Why

A failed adoption followed by a name conflict could create a second, empty workspace. Each runtime replica then cached its own sandbox for the same thread, and subsequent runs could alternate between them. The backend now rejects a reported name that differs from the thread's existing MDA binding, rather than overwriting it. Existing split bindings require deliberate recovery; this change does not choose, copy or delete either workspace.

## Upstream

MDA should distinguish not-found from unreachable, never abandon an existing thread's work after an adoption failure, and persist a single authoritative binding across replicas. Once that contract includes explicit replacement and stale-cache reconciliation, Open SWE can remove this policy flag and use the platform's binding directly.
