"""Open SWE's pull request reviewer as a Managed Deep Agent.

The model loop and the sandbox run here. The Open SWE backend prepares every
run, holds every credential, and serves the reviewer's tools over MCP; this
deployment reaches it with the run token Open SWE puts in each run's context.
"""

from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from managed_deepagents import (
    DeepAgentDefinition,
    ManagedServerRuntime,
    define_deep_agent,
    define_sandbox,
)
from open_swe_reviewer.backend import MCP_SERVER_NAME, OpenSweBackend
from open_swe_reviewer.middleware import BackendRunMiddleware, SubagentRunMiddleware
from open_swe_reviewer.tools import runtime_spec, sandbox_tools
from pydantic import BaseModel

# Matches the in-process reviewer's cap on model calls per run.
_MODEL_CALL_LIMIT = 5_000
# Callable from code mode, so large results can be filtered or written to files without
# passing through the model.
_PTC_TOOLS: list[str | BaseTool] = [
    "read_file",
    "write_file",
    *(
        f"{MCP_SERVER_NAME}_{tool}"
        for tool in (
            "web_search",
            "fetch_url",
            "http_request",
            "list_findings",
            "add_finding",
            "update_finding",
            "publish_review",
            "resolve_finding_thread",
            "reply_to_finding_thread",
        )
    ),
]


class ReviewerRunContext(BaseModel):
    """What Open SWE dispatch passes as the run context."""

    run_token: str | None = None
    snapshot_id: str | None = None


def agent(runtime: ManagedServerRuntime) -> DeepAgentDefinition:
    execution = runtime.execution_runtime
    context = ReviewerRunContext.model_validate(
        execution.context if execution is not None and execution.context is not None else {}
    )
    backend = OpenSweBackend(context.run_token)
    subagent = runtime_spec()["subagent"]
    return define_deep_agent(
        name="reviewer",
        # Every model call runs on the model the backend picked for the run; this one
        # only backs deepagents' own summarization.
        model="anthropic:claude-opus-5-5",
        context_schema=ReviewerRunContext,
        tools=sandbox_tools(),
        mcp=[backend.mcp()],
        # Boots from the workspace's snapshot, as Open SWE's own reviewer sandboxes do.
        sandbox=define_sandbox(snapshot_id=context.snapshot_id)
        if context.snapshot_id
        else define_sandbox(),
        subagents=[
            {
                "name": subagent["name"],
                "description": subagent["description"],
                "system_prompt": subagent["system_prompt"],
                "middleware": [
                    SubagentRunMiddleware(),
                    ModelRetryMiddleware(retry_on=(TimeoutError,)),
                ],
            }
        ],
        middleware=[
            BackendRunMiddleware(backend),
            CodeInterpreterMiddleware(ptc=_PTC_TOOLS),
            ModelCallLimitMiddleware(run_limit=_MODEL_CALL_LIMIT, exit_behavior="end"),
            ModelRetryMiddleware(retry_on=(TimeoutError,)),
        ],
    )
