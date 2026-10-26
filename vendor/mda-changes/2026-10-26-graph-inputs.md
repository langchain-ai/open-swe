# `graph_inputs`: one graph per thread, for its runs and its reads

**Verdict: upstream as-is.**

**Changed:** `managed_deepagents/define_deep_agent.py` (`graph_inputs`), `_managed_tools.py` (`graph_inputs` on the managed server runtimes), `runtime.py` (`_ThreadGraph`, `_BuiltGraph`, `_for_call`; `_compile_managed_agent` returns the graph and its telemetry configuration instead of wrapping it).

## What

A factory may return `define_deep_agent(..., graph_inputs={...})`: a JSON object naming everything it built the definition from. It promises that the same inputs build the same agent for every run, with nothing that belongs to one run in its tools or middleware. For such a factory:

- A run saves its inputs in the deployment's store: the inputs under their digest (`("mda", "graph_inputs")`), and the digest under the thread (`("mda", "thread_graphs", <graph>)`).
- A read (`threads.read`, `assistants.read`, no execution runtime) of a thread that ran calls the factory with those inputs (`runtime.graph_inputs`), so it gets the graph the thread's runs write it with. A thread that never ran reads as before.
- Each process keeps the compiled graph of its 128 most recent threads, keyed by graph, thread and digest, and for a run also by the caller's memory access. Runs and reads reuse it; it is rebuilt only when the inputs change. The run's telemetry wrapper and config defaults (`graph_default_config`, routing configurable) are applied per call to a copy, never to the kept graph.

A factory that declares no `graph_inputs` compiles as before; its reads cost one extra store read.

## Why

MDA ran the factory and `create_deep_agent` on every graph access: about 2.1s per run in Open SWE's production deployment, and 100–500ms per `/threads/{id}/state` and `/stream/events` read, dozens per second from one dashboard. Reads had no run context, so the factory built them a different graph from the run's: without code mode's state (`REPLState`), the run's skills, subagents and tools. LangGraph then read each checkpoint through a graph with other channels than the one that wrote it.

## Upstream

Propose `graph_inputs` and the per-thread cache as they are; once MDA has them, drop this note. Open questions for upstream: whether reads should also skip the Context Hub (they still build with `inspection=True`), and whether the cache size should be configurable.
