# Terminology and identifiers

Open SWE uses this product model:

- **Session**: one durable conversation and work context across messages and follow-ups. A session can contain multiple invocations.
- **Invocation**: one execution of Open SWE within a session. Model attribution, execution status, cost, and duration are invocation-level measurements.
- **Trace**: a LangSmith diagnostic execution tree. One invocation can correlate with multiple root traces, and each trace contains nested LangSmith runs such as model and tool calls.

Use **LangGraph thread**, **LangGraph run**, **LangSmith thread**, or **LangSmith run** when referring to those systems' native concepts. An Open SWE session is currently persisted as a LangGraph thread, and an invocation executes as a LangGraph run, but those implementation terms are not product synonyms.

## Identifier mapping

| Concept | Canonical application identifier | Compatibility or external identifiers |
|---|---|---|
| Session | The application currently uses the LangGraph `thread_id` value | Keep `thread_id` at LangGraph, storage, URL, and protocol boundaries |
| Invocation | `invocation_id` | `prepare_run_id` is dual-read and dual-written during migration; usage storage retains the `usage/v2/agent_runs` namespace, `run:*` keys, and legacy `run_id` field |
| LangGraph run | `langgraph_run_id` in application locals where disambiguation is needed | Preserve native SDK and webhook field `run_id` |
| LangSmith run or trace | `langsmith_run_id` or `trace_id` where available | Preserve native LangSmith API field names such as `session_id`, which means project ID in the stats API |

When `invocation_id` and `prepare_run_id` coexist, they must contain the same non-empty value. Conflicting values are rejected or treated as unavailable; Open SWE does not generate a replacement or correlate unrelated executions. Historical records and traces containing only `prepare_run_id` remain readable. New launches temporarily write both fields, and LangSmith correlation queries both fields while retaining support for multiple traces per invocation.

A later migration may stop writing `prepare_run_id` after all queued jobs, callbacks, clients, and historical lookup requirements have aged out. Physical storage namespaces and keys do not need renaming for that migration.
