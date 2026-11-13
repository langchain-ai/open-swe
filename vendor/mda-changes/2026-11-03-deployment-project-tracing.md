# Deployment-project-only tracing

**Upstream as-is.**

**What:** `define_deep_agent(trace_only_deployment_project=True)` opts into filtering Agent Server replicas to `LANGSMITH_PROJECT` when a run carries `__langsmith_project__`.
`runtime.py` selects the compiled graph variant; `_run_input.py` scopes tracing around synchronous and asynchronous execution, tracer setup and stream cleanup, copying the parent with the same filtered replicas.
Runs without a requested project and agents that omit the flag retain their existing tracing destinations.

**Why:** Open SWE no longer requests its backend tracing project, but existing threads or subscriptions can still carry that request and duplicate runs into both projects.
Open SWE enables the guard in `mda/agent.py` so those stored requests cannot duplicate execution spans.

**Upstream:** Keep this guard until the SDK supports deployment-only tracing and Open SWE uses it.
Removing the guard requires every requester to stop sending the backend project first, including clearing or migrating stored thread and subscription requests; their storage location still needs verification.
