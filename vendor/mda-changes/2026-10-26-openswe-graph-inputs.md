# `_openswe_graph_inputs`, `_openswe_select` and `_openswe_without_box`: one graph for every run and read

**Verdict: not upstream as-is.** MDA should compile graphs once and share them on its own, with no cache key from the author and no per-run sandbox hook. It can choose that interface itself.

**Changed:** `managed_deepagents/define_deep_agent.py` (`_openswe_graph_inputs`), `namespaces.py` and `sandbox.py` (`_openswe_select`, `selected_for`, `_openswe_without_box`, `without_box`), `runtime.py`:
- `_SharedGraphs` and `_BuiltGraph`;
- `_for_call`;
- `_compile_managed_agent` returns the graph and its telemetry configuration instead of wrapping it;
- `_LazyManagedSandboxBackend` resolves the run's box per call, and acts on `_openswe_without_box` for a run without one.

## What

- **One graph per declared inputs.** A factory may return `define_deep_agent(..., _openswe_graph_inputs={...})`: a JSON object naming everything it built the definition from. It promises that the same inputs build the same agent for every run, thread and caller, with nothing that belongs to one of them in its tools, middleware or sandbox.
- **Compiled once, shared by everything.** MDA compiles such a definition once per process for each graph (the lead's or an async subagent's) and those inputs. It compiles without any run's config and shares the graph between every run and read. A run's graph is also keyed by the caller's memory access. The run's telemetry wrapper and config defaults (`graph_default_config`, routing configurable) are applied per call to a copy.
- **The run picks its box.** `define_sandbox(_openswe_select=...)` names a function from a run's runtime to the box definition that run uses, or None for a run without one.
- **A run without a box acts elsewhere.** `define_sandbox(_openswe_without_box=...)` names the backend a run acts on when `_openswe_select` gives it no box. The graph's own uses of its backend, such as offloading a large tool result, go there instead of failing the run.
- **The box is resolved per call.** The lazy sandbox backend resolves the box from the bound run, or for the unbound backend the composite mounts, from the run in progress. A shared graph's unbound backend keeps no box of its own, so it never serves one thread from another's box.

Open SWE's inputs are the graph a run plays and where its workers report, so a process builds at most a few graphs. Every run's catalog is read at run time instead.

## Why

MDA ran the factory and `create_deep_agent` on every graph access: about 2.1s per run in Open SWE's production deployment. `/threads/{id}/state` reads, dozens per second, took only 100–500ms because they built the wrong graph; built correctly, each would have cost a run's compile.

Reads had no run context, so the factory built them a different graph from the run's. It lacked code mode's state, the run's skills, subagents and tools, so LangGraph read each checkpoint through a graph with other channels than the one that wrote it.

The Agent Server's `ServerRuntime` already requires every access to return the same topology.

A shared graph declares a sandbox even for runs that have none, such as a thread whose box Open SWE's backend owns or one bridged to a person's machine. Their file tools act on the backend's files, but deepagents' own offload of a large tool result went to the graph's sandbox and ended the run with "This run has no sandbox". With `_openswe_without_box`, it goes to the backend's files under the run's artifacts root, where those tools read it.

## Upstream

**What MDA should build:**
- Compile a definition once per deployment revision and reuse it across runs, threads and reads.
- Give a run's per-run values (tokens, the box's options, a worker's coordinator thread) to tools, middleware and the sandbox from the run context at call time.
- Never close a compiled graph over one run's config.

**What changes for Open SWE once MDA has it:**
- Its factory becomes a static definition. Per-run reads already go through the run context (`rt.backend.current_run`).
- Its box options, and where a run without a box keeps the graph's files, move to whatever per-run sandbox selection MDA offers.
- Then this note goes.
