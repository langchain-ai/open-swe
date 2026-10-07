"""Open SWE's pull request reviewer as a Managed Deep Agent.

The model loop and the sandbox run here. The Open SWE backend prepares every
run, holds every credential, and serves the reviewer's tools over MCP; this
deployment reaches it with the run token Open SWE stamps on each run it starts.
"""

from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from managed_deepagents import (
    DeepAgentDefinition,
    ManagedServerRuntime,
    define_deep_agent,
    define_sandbox,
)
from open_swe_reviewer.middleware import BackendRunMiddleware, SubagentRunMiddleware
from open_swe_reviewer.tools import reviewer_tools, runtime_spec
from pydantic import BaseModel

# Matches the in-process reviewer's cap on model calls per run.
_MODEL_CALL_LIMIT = 5_000


class ReviewerRunContext(BaseModel):
    """What Open SWE dispatch passes as the run context."""

    snapshot_id: str | None = None


def agent(runtime: ManagedServerRuntime) -> DeepAgentDefinition:
    execution = runtime.execution_runtime
    context = ReviewerRunContext.model_validate(
        execution.context if execution is not None and execution.context is not None else {}
    )
    subagent = runtime_spec()["subagent"]
    return define_deep_agent(
        name="reviewer",
        # Every model call runs on the model the backend picked for the run; this one
        # only backs deepagents' own summarization.
        model="anthropic:claude-opus-5-5",
        context_schema=ReviewerRunContext,
        tools=reviewer_tools(),
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
            BackendRunMiddleware(),
            ModelCallLimitMiddleware(run_limit=_MODEL_CALL_LIMIT, exit_behavior="end"),
            ModelRetryMiddleware(retry_on=(TimeoutError,)),
        ],
    )
