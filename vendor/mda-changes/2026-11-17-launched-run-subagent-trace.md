# Upstream as-is.

`packages/pypi/src/managed_deepagents/runtime.py`: `_parent_trace` nests a
marked run under its distributed parent. Only a run with an async worker
reference (`configurable.deepagents_parent.thread_id`, or the Agent Server's
`metadata.parent_thread_id` fallback) receives `ls_agent_type: subagent`.
The nesting marker alone does not identify a worker: dashboard sends use it too.
Those sends keep their parent without receiving a subagent type.

Robin tags its agent's own model calls `ls_agent_type: root`
(`RootAgentTraceMiddleware` in `mda/rt/tracing.py`), so dashboard turns remain
in the main conversation across summaries. The middleware preserves the
`subagent` type on actual workers nested inside their coordinator's trace.

This corrects the blanket classification introduced in
langchain-ai/langchainplus#64173. The matching canonical Python and TypeScript
SDK fix is tracked in https://github.com/langchain-ai/langchainplus/pull/67122.
Drop this vendored change once `uv run scripts/vendor.py` reaches that export.
`test_an_agents_own_calls_are_its_root_agent_and_a_launched_runs_are_a_subagents`
and `test_a_distributed_parent_keeps_graph_nodes_under_the_graph_run` hold the
classification and parenting behavior.
