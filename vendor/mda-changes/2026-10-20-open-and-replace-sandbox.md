# `open_sandbox`

**Verdict: upstream as-is.**
Open the run's managed sandbox before its first tool call.

**Changed:** `managed_deepagents/sandbox.py` (`open_sandbox`), `runtime.py` (`_LazyManagedSandboxBackend.aopen`, `_managed_sandbox_of`), `__init__.py` (exports).

## What

`open_sandbox(runtime.backend)` adopts or creates the run's sandbox now, as its first use would, and returns its name.

## Why

Open SWE's backend works in the same sandbox as MDA, so it needs the sandbox's name before the run prepares.

## Upstream

As it stands, unchanged.
