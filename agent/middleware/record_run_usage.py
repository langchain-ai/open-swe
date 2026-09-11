"""Persist usage for completed turns and terminal model failures."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentState, ModelRequest, ModelResponse
from langchain_core.exceptions import ModelAuthenticationError
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent.agent_cost import finalize_agent_invocation_usage
from agent.middleware.trace import OpenSWEMiddleware
from agent.run_config import RunConfig


class RecordRunUsageMiddleware(OpenSWEMiddleware):
    """Tag model responses with their invocation and persist usage on completion."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            response = await handler(request)
        except Exception as exc:
            cfg = RunConfig.from_runtime()
            if cfg.invocation_id and cfg.thread_id:
                await finalize_agent_invocation_usage(
                    invocation_id=cfg.invocation_id,
                    thread_id=cfg.thread_id,
                    state=dict(request.state),
                    status="error",
                    failure_code=(
                        "authentication_rejected"
                        if isinstance(exc, ModelAuthenticationError)
                        or getattr(exc, "status_code", None) == 401
                        else None
                    ),
                )
            raise
        invocation_id = RunConfig.from_runtime().invocation_id
        if invocation_id:
            for message in response.result:
                if isinstance(message, AIMessage):
                    message.response_metadata = {
                        **message.response_metadata,
                        "open_swe_invocation_id": invocation_id,
                        "open_swe_run_id": invocation_id,
                    }
        return response

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del runtime
        cfg = RunConfig.from_runtime()
        if not cfg.invocation_id or not cfg.thread_id:
            return None
        await finalize_agent_invocation_usage(
            invocation_id=cfg.invocation_id,
            thread_id=cfg.thread_id,
            state=dict(state),
        )
        return None


record_run_usage = RecordRunUsageMiddleware()
