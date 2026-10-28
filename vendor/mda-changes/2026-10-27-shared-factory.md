# `shared_factory`: one graph per distinct inputs

**Verdict: upstream as-is.**
Any project whose agent varies per run in a few ways, such as its model, needs it, and it replaces an author-declared cache key MDA had to trust.

**Changed:** `managed_deepagents/agent_entry.py` (`SharedAgentFactory`, `SharedInputs`, `shared_factory`), `__init__.py` (exports), `runtime.py`:
- `_SharedDefinitions`, `_SharedGraphs` and `_BuiltGraph`;
- `compile_managed_agent_async` resolves a shared factory's inputs before any build;
- `_for_call`;
- `_compile_managed_agent` returns the graph and its telemetry configuration instead of wrapping it.

## What

A factory splits in two: what the agent is built from, and the build.

```python
async def agent_inputs(runtime: ManagedServerRuntime) -> AgentInputs: ...

@shared_factory(agent_inputs)
def agent(inputs: AgentInputs) -> DeepAgentDefinition:
    return define_deep_agent(name="assistant", model=inputs.model)
```

- **The inputs are the key.**
  `agent_inputs` runs on every access, a run's or a read's, and returns a pydantic model.
  MDA keys the definition on a digest of its JSON.
- **The build sees only the key.**
  MDA calls `agent` only for inputs it keeps no definition for yet, with a copy of the inputs made from that JSON, in an empty context: no runtime, no run in progress.
  Nothing of the first run on some inputs can reach the graph every later run on them shares.
- **Compiled once, shared by everything.**
  MDA compiles each definition once per process for each graph (the lead's or an async subagent's) and shares it between every run and read with the same inputs.
  A run's graph is also keyed by the caller's memory access.
  The run's telemetry wrapper and config defaults (`graph_default_config`, routing configurable) are applied per call to a copy.
  A process keeps up to 32 definitions and 32 graphs.
- `mda build` reads the decorated `agent` as any factory: assignments, then `return define_deep_agent(...)`.

## Why

MDA ran the factory and `create_deep_agent` on every graph access: about 2.1s per run in Open SWE's production deployment.
Reads built a different graph from runs, so LangGraph read each checkpoint through a graph with other channels than the one that wrote it.

The first fix had the factory run on every access with the full runtime and declare a cache key on the definition it returned (`_openswe_graph_inputs`).
MDA trusted that the key named everything the definition was built from.
It didn't: the run's model was missing, so deepagents' summarizer and harness profile came from the graph's placeholder model for every run, and long threads on another provider failed with `Anthropic authentication failed`.
Here the key is all the build is given, so the two can't diverge, and a cached access never runs the build.

A single static definition isn't enough: deepagents fixes the summarizer, the harness profile and any state that profile adds when the graph compiles, so runs on different models need different graphs.

## Upstream

As it stands.
A TypeScript equivalent would take the same two functions.

**What changes for Open SWE once MDA has it:** nothing, and this note goes.
