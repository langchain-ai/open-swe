# `open_sandbox` and `replace_sandbox`

**Verdict: upstream as-is.** Generic operations on the run's managed box.

**Changed:** `managed_deepagents/sandbox.py` (`open_sandbox`, `replace_sandbox`, `SandboxReplacement`), `runtime.py` (`_LazyManagedSandboxBackend.aopen` / `areplace`, `_replace_managed_sandbox`, `_managed_sandbox_of`), `__init__.py` (exports).

## What

- `open_sandbox(runtime.backend)` adopts or creates the run's box now, as its first use would, and returns its name.
- `replace_sandbox(runtime.backend, definition)` gives the thread a fresh box under the same name, created from `definition`'s options. The old box is renamed (keeping the deployment prefix) and stopped, not deleted.

## Why

Open SWE's backend works in the same box as MDA, so it needs the box's name before the run prepares, and its `recreate_sandbox` tool gives a thread a fresh box.

## Upstream

Both as they stand, unchanged.
