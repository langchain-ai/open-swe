# `_openswe_select` and `_openswe_without_sandbox`: the run picks its sandbox

**Verdict: not upstream as-is.**
MDA should resolve a shared graph's sandbox per run on its own, with no per-run sandbox hook from the author.

**Changed:** `namespaces.py` and `sandbox.py` (`_openswe_select`, `selected_for`, `_openswe_without_sandbox`, `without_sandbox`), `runtime.py` (`_LazyManagedSandboxBackend` resolves the run's sandbox per call, and acts on `_openswe_without_sandbox` for a run without one).

This change first also declared the shared graph's cache key on the definition (`_openswe_graph_inputs`).
That is now `shared_factory`: see [`2026-10-27-shared-factory.md`](2026-10-27-shared-factory.md).

## What

- **The run picks its sandbox.**
  `define_sandbox(_openswe_select=...)` names a function from a run's runtime to the sandbox definition that run uses, or None for a run without one.
- **A run without a sandbox acts elsewhere.**
  `define_sandbox(_openswe_without_sandbox=...)` names the backend a run acts on when `_openswe_select` gives it no sandbox.
  The graph's own uses of its backend, such as offloading a large tool result, go there instead of failing the run.
- **The sandbox is resolved per call.**
  The lazy sandbox backend resolves the sandbox from the bound run, or for the unbound backend the composite mounts, from the run in progress.
  A shared graph's unbound backend keeps no sandbox of its own, so it never serves one thread from another's sandbox.

## Why

A shared graph is compiled once for many runs (`shared_factory`), but each Open SWE run's sandbox options come from its own catalog: the thread's workspace snapshot, VM size and TTLs.
The graph can't close over one run's sandbox.

A shared graph declares a sandbox even for runs that have none, such as a thread whose sandbox Open SWE's backend owns or one bridged to a person's machine.
Their file tools act on the backend's files, but deepagents' own offload of a large tool result went to the graph's sandbox and ended the run with "This run has no sandbox".
With `_openswe_without_sandbox`, it goes to the backend's files under the run's artifacts root, where those tools read it.

## Upstream

**What MDA should build:** give a run's per-run sandbox options to the sandbox from the run context at call time, so a definition compiled once serves every run's own sandbox.

**What changes for Open SWE once MDA has it:** its sandbox options, and where a run without a sandbox keeps the graph's files, move to whatever per-run sandbox selection MDA offers, and this note goes.
