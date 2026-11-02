# Keep `_openswe_per_thread` as the mark of a thread's own sandbox

**Verdict: not upstream as-is.** Upstream MDA now keeps a live thread's sandbox when a dynamic selection's options change, so the option's original purpose (2026-10-20, dropped when MDA `main` was vendored) is gone.

**Changed:** `managed_deepagents/namespaces.py` (`define_sandbox` accepts `_openswe_per_thread`), `sandbox.py` (`is_per_thread`, and the option counts as a resolution option), `runtime.py` (`_create_langsmith_sandbox` imports `is_per_thread`). Open SWE's `mda/rt/managed_sandbox.py` passes it on both of its sandbox definitions.

## What

`is_per_thread` only marks a sandbox as a thread's own. The 2026-10-29 policies (preserve-thread-sandbox, authoritative-sandbox-binding, deleted-bound-sandbox) key on it: never create a second workspace for a thread, only reconnect to an existing replacement, never replace the sandbox implicitly after a tool error. It no longer changes how a sandbox is selected or keyed.

## Upstream

Once MDA persists one authoritative binding per thread and never abandons a thread's sandbox (see those notes), Open SWE stops passing the option.
