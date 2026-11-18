# Upstream as-is.

`packages/pypi/src/managed_deepagents/runtime.py`: `_parent_trace` traces a run
it nests under the async subagent call that launched it with
`ls_agent_type: subagent`.

Robin tags its agent's own model calls `ls_agent_type: root`
(`RootAgentTraceMiddleware` in `mda/rt/tracing.py`), so LangSmith keeps a
thread's turns in one main conversation across summaries. A worker's first run
is traced inside its coordinator's trace, and without a type of its own it
would be tagged `root` too and join the coordinator's conversation. The
middleware keeps a type a call already has.

Upstream: this is the Python half of langchain-ai/langchainplus#66339, which MDA
merged and exports to the SDK's `main`. Drop this change once
`uv run scripts/vendor.py` reaches that export;
`test_an_agents_own_calls_are_its_root_agent_and_a_launched_runs_are_a_subagents`
holds the behavior.
