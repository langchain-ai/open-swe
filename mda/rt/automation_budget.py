"""Enforce automation budgets around model calls without coupling them to reporting."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import messages_to_dict
from langgraph.runtime import Runtime
from pydantic import JsonValue, TypeAdapter
from runtime_api.endpoint import call
from runtime_api.loop import AUTOMATION_USAGE, AutomationUsage

from rt.backend import OpenSweBackend
from rt.tools import run_id

_json_dicts = TypeAdapter(list[dict[str, JsonValue]])


class AutomationBudgetMiddleware(AgentMiddleware):
    """Check the shared budget before a call and synchronously account for its response."""

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, object] | None:
        if not self._backend.run.catalog.loop.automation:
            return None
        allowance = await call(self._backend, AUTOMATION_USAGE, AutomationUsage(run_id=run_id()))
        return None if allowance.allowed else {"jump_to": "end"}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = await handler(request)
        if self._backend.run.catalog.loop.automation:
            await call(
                self._backend,
                AUTOMATION_USAGE,
                AutomationUsage(
                    run_id=run_id(),
                    response=_json_dicts.validate_python(messages_to_dict(response.result)),
                ),
            )
        return response
