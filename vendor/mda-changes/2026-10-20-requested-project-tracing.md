# Trace only into the requested project

**Upstream as-is.**

- **What:** `define_deep_agent(replicate_traces=False)` opts into keeping only the requested project when Agent Server supplies tracing replicas for a per-run project override. `define_deep_agent.py` records the author-owned option, `runtime.py` selects the compiled-agent policy, and `_run_input.py` scopes the tracing context around graph execution. The default remains replication; runs without a requested project keep their existing tracing behavior. Existing replica credentials and updates, parent IDs, callbacks, and complete graph/model traces are preserved without mutating the surrounding context.
- **Why:** Open SWE requests its backend's canonical tracing project for MDA runs. Agent Server otherwise records the same spans in both that project and the deployment project, double-counting model usage when projects are aggregated. Open SWE opts out in `mda/agent.py`; it does not disable tracing or suppress distinct model calls.
- **Upstream:** MDA should offer an author-owned policy for requested-project-only tracing. Drop this patch when the SDK supports that behavior, or when Agent Server exposes a supported equivalent that MDA can use.
