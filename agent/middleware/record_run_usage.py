"""Persist usage for completed turns and terminal model failures."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentState, ModelRequest, ModelResponse
from langchain_core.exceptions import ModelAuthenticationError
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from agent.agent_cost import finalize_agent_invocation_usage
from agent.middleware.trace import OpenSWEMiddleware
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


def _tag_run_metadata(extra_metadata: dict[str, Any]) -> None:
    """Attach ``extra_metadata`` to the current LangSmith run, best-effort."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run_tree = get_current_run_tree()
    except Exception:  # noqa: BLE001
        run_tree = None
    if run_tree is None:
        return
    try:
        run_tree.metadata.update(extra_metadata)
    except Exception:  # noqa: BLE001
        logger.debug("Could not tag run metadata", exc_info=True)


class RecordRunUsageMiddleware(OpenSWEMiddleware):
    """Tag model responses with their invocation and persist usage on completion."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model_route = request.state.get("model_route")
        if isinstance(model_route, str) and model_route:
            _tag_run_metadata({"open_swe_model_route": model_route})
        try:
            response = await handler(request)
        except Exception as exc:
            cfg = RunConfig.from_runtime()
            if cfg.invocation_id and cfg.thread_id:
                await finalize_agent_invocation_usage(
                    invocation_id=cfg.invocation_id,
                    thread_id=cfg.thread_id,
                    invocation_started_at=cfg.invocation_started_at,
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
        for message in response.result:
            if isinstance(message, AIMessage):
                message.response_metadata = {
                    **message.response_metadata,
                    **({"open_swe_model_route": model_route} if model_route else {}),
                    **(
                        {"open_swe_invocation_id": invocation_id, "open_swe_run_id": invocation_id}
                        if invocation_id
                        else {}
                    ),
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
            invocation_started_at=cfg.invocation_started_at,
            state=dict(state),
        )
        return None


record_run_usage = RecordRunUsageMiddleware()
