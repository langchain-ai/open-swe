# Upstream as-is.

`packages/pypi/src/managed_deepagents/runtime.py`: `_parent_trace` nests a
marked run under its distributed parent and sets nothing else in the tracing
context. Setting `ls_agent_type: subagent` there as run-wide tracing metadata
(langchain-ai/langchainplus#64173) made the Agent Server trace each graph node
of an async worker as a separate root instead of under the worker's graph run.

Robin tags its agent's own model calls instead (`RootAgentTraceMiddleware` in
`mda/rt/tracing.py`): `subagent` on an async worker's calls (a run with
`configurable.deepagents_parent.thread_id` or `metadata.parent_thread_id`), so
they stay out of the coordinator's conversation, and `root` on every other
agent's calls, so dashboard turns remain one conversation across summaries.

The canonical SDK still sets the run-wide tag
(https://github.com/langchain-ai/langchainplus/pull/67122 keeps it for workers).
Drop this vendored change once `uv run scripts/vendor.py` reaches an export
that nests without it.
`test_an_agents_own_calls_are_its_root_agent_and_a_launched_runs_are_a_subagents`
and `test_a_distributed_parent_keeps_graph_nodes_under_the_graph_run` hold the
classification and parenting behavior.
