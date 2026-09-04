"""After-agent middleware that persists run usage telemetry."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent.agent_cost import finalize_agent_run_usage
from agent.run_config import RunConfig


class RecordRunUsageMiddleware(AgentMiddleware):
    """Tag model responses with their run and persist usage on completion."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        run_id = RunConfig.from_runtime().prepare_run_id
        if run_id:
            for message in response.result:
                if isinstance(message, AIMessage):
                    message.response_metadata = {
                        **message.response_metadata,
                        "open_swe_run_id": run_id,
                    }
        return response

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del runtime
        cfg = RunConfig.from_runtime()
        if not cfg.prepare_run_id or not cfg.thread_id:
            return None
        await finalize_agent_run_usage(
            run_id=cfg.prepare_run_id,
            thread_id=cfg.thread_id,
            state=dict(state),
        )
        return None


record_run_usage = RecordRunUsageMiddleware()
