# `open_sandbox` and `replace_sandbox`

**Verdict: upstream as-is.** Generic operations on the run's managed sandbox.

**Changed:** `managed_deepagents/sandbox.py` (`open_sandbox`, `replace_sandbox`, `SandboxReplacement`), `runtime.py` (`_LazyManagedSandboxBackend.aopen` / `areplace`, `_replace_managed_sandbox`, `managed_sandbox_of`, public so `sandbox.py` can import it), `__init__.py` (exports), `tests/test_authoring.py` (the exports).

## What

- `open_sandbox(runtime.backend)` adopts or creates the run's sandbox now, as its first use would, and returns its name.
- `replace_sandbox(runtime.backend, definition)` gives the thread a fresh sandbox under the same name, created from `definition`'s options. The old sandbox is renamed (keeping the deployment prefix) and stopped, not deleted.

## Why

Open SWE's backend works in the same sandbox as MDA, so it needs the sandbox's name before the run prepares, and its `recreate_sandbox` tool gives a thread a fresh sandbox.

## Upstream

Both as they stand, unchanged.
