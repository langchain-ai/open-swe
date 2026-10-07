"""Open SWE's pull request reviewer as a Managed Deep Agent.

Only the model loop runs here. The Open SWE backend prepares every run, owns the
sandbox and every credential, and serves the reviewer's tools over MCP; this
deployment reaches it with the run token Open SWE stamps on each run it starts.
"""

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from managed_deepagents import define_deep_agent
from open_swe_reviewer.middleware import BackendRunMiddleware, SubagentRunMiddleware
from open_swe_reviewer.sandbox import RunSandboxBackend
from open_swe_reviewer.tools import backend_tools, runtime_spec

# Matches the in-process reviewer's cap on model calls per run.
_MODEL_CALL_LIMIT = 5_000

_sandbox = RunSandboxBackend()
_subagent = runtime_spec()["subagent"]

agent = define_deep_agent(
    name="reviewer",
    # Every model call runs on the model the backend picked for the run; this one
    # only backs deepagents' own summarization.
    model="anthropic:claude-opus-5-5",
    tools=backend_tools(),
    subagents=[
        {
            "name": _subagent["name"],
            "description": _subagent["description"],
            "system_prompt": _subagent["system_prompt"],
            "middleware": [
                FilesystemMiddleware(backend=_sandbox, offload_binary_content=True),
                SubagentRunMiddleware(),
                ModelRetryMiddleware(retry_on=(TimeoutError,)),
            ],
        }
    ],
    middleware=[
        # Replaces deepagents' own filesystem middleware by name, so file and shell
        # tools work in the backend's sandbox instead of the managed one.
        FilesystemMiddleware(backend=_sandbox, offload_binary_content=True),
        BackendRunMiddleware(),
        ModelCallLimitMiddleware(run_limit=_MODEL_CALL_LIMIT, exit_behavior="end"),
        ModelRetryMiddleware(retry_on=(TimeoutError,)),
    ],
)
