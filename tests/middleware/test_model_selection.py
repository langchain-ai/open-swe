from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from agent.middleware.model_selection import ModelSelectionMiddleware


@tool
async def read_file(file_path: str) -> str:
    """Read a file."""
    return file_path


@tool
async def edit_file(file_path: str) -> str:
    """Edit a file."""
    return file_path


@tool
async def exit_routing_mode(model_route: str) -> str:
    """Exit routing."""
    return model_route


def _middleware() -> tuple[ModelSelectionMiddleware, dict[str, MagicMock]]:
    models = {profile: MagicMock(name=profile) for profile in ("fast", "balanced", "performance")}
    return ModelSelectionMiddleware(cast(Any, models)), models


async def _invoke(middleware: ModelSelectionMiddleware, state: dict[str, Any]) -> ModelRequest:
    request = ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content="Update the README")],
        tools=[read_file, edit_file, exit_routing_mode],
        state=cast(Any, state),
    )
    seen: list[ModelRequest] = []

    async def handler(routed: ModelRequest) -> ModelResponse:
        seen.append(routed)
        return MagicMock()

    await middleware.awrap_model_call(request, handler)
    return seen[0]


@pytest.mark.asyncio
async def test_routing_mode_starts_on_fast_with_read_only_tools() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {}

    state.update(middleware.before_agent(state, MagicMock()))
    request = await _invoke(middleware, state)

    assert state == {"routing_mode": True, "model_route": "fast"}
    assert request.model is models["fast"]
    assert [tool.name for tool in request.tools] == ["read_file", "exit_routing_mode"]
    assert request.system_message is not None
    assert "Routing Mode (ACTIVE)" in request.system_message.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["fast", "balanced", "performance"])
async def test_selected_route_activates_model_and_hides_exit_tool(route: str) -> None:
    middleware, models = _middleware()

    request = await _invoke(
        middleware,
        {"routing_mode": False, "model_route": route},
    )

    assert request.model is models[route]
    assert [tool.name for tool in request.tools] == ["read_file", "edit_file"]


@pytest.mark.asyncio
async def test_unknown_selected_route_falls_back_to_balanced() -> None:
    middleware, models = _middleware()

    request = await _invoke(
        middleware,
        {"routing_mode": False, "model_route": "unknown"},
    )

    assert request.model is models["balanced"]
