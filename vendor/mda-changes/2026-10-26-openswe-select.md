# `_openswe_select`: the run picks its box

**Verdict: not upstream as-is.**
MDA should resolve a shared graph's box per run on its own, with no per-run sandbox hook from the author.

**Changed:** `namespaces.py` and `sandbox.py` (`_openswe_select`, `selected_for`), `runtime.py` (`_LazyManagedSandboxBackend` resolves the run's box per call).

This change first also declared the shared graph's cache key on the definition (`_openswe_graph_inputs`).
That is now `shared_factory`: see [`2026-10-27-shared-factory.md`](2026-10-27-shared-factory.md).

## What

- **The run picks its box.**
  `define_sandbox(_openswe_select=...)` names a function from a run's runtime to the box definition that run uses, or None for a run without one.
- **The box is resolved per call.**
  The lazy sandbox backend resolves the box from the bound run, or for the unbound backend the composite mounts, from the run in progress.
  A shared graph's unbound backend keeps no box of its own, so it never serves one thread from another's box.

## Why

A shared graph is compiled once for many runs (`shared_factory`), but each Open SWE run's box options come from its own catalog: the thread's workspace snapshot, VM size and TTLs.
The graph can't close over one run's box.

## Upstream

**What MDA should build:** give a run's per-run sandbox options to the sandbox from the run context at call time, so a definition compiled once serves every run's own box.

**What changes for Open SWE once MDA has it:** its box options move to whatever per-run sandbox selection MDA offers, and this note goes.
