from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage

from agent.middleware.model_selection import ModelSelectionMiddleware

_TOOLS = [{"name": name} for name in ("exit_pre_routed_mode", "execute", "read_file", "edit_file")]


def _middleware(
    initial_route: Any = None,
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock]]:
    models = {profile: MagicMock(name=profile) for profile in ("fast", "balanced", "performance")}
    return ModelSelectionMiddleware(cast(Any, models), initial_route=initial_route), models


async def _model_call(middleware: ModelSelectionMiddleware, state: dict[str, Any]) -> ModelRequest:
    request = ModelRequest(
        model=MagicMock(),
        messages=state.get("messages", []),
        tools=list(_TOOLS),
        state=cast(Any, state),
    )
    seen: list[ModelRequest] = []

    async def handler(routed: ModelRequest) -> ModelResponse:
        seen.append(routed)
        return MagicMock()

    await middleware.awrap_model_call(request, handler)
    return seen[0]


async def _tool_call(
    middleware: ModelSelectionMiddleware, state: dict[str, Any], name: str
) -> tuple[bool, ToolMessage | Any]:
    request = ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call-1", "type": "tool_call"},
        tool=cast(Any, MagicMock()),
        state=cast(Any, state),
        runtime=cast(Any, MagicMock()),
    )
    ran: list[bool] = []

    async def handler(_: ToolCallRequest) -> ToolMessage:
        ran.append(True)
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = await middleware.awrap_tool_call(request, handler)
    return bool(ran), result


@pytest.mark.asyncio
async def test_new_thread_starts_pre_routed_on_the_fast_model_with_the_full_tool_list() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))
    routed = await _model_call(middleware, state)

    assert state["pre_routed"] is True
    assert "model_route" not in state
    assert routed.model is models["fast"]
    assert routed.tools == _TOOLS


@pytest.mark.asyncio
async def test_pre_routed_mode_rejects_mutating_tools_but_allows_reads_and_the_exit() -> None:
    middleware, _ = _middleware()
    state: dict[str, Any] = {"pre_routed": True}

    ran, result = await _tool_call(middleware, state, "edit_file")
    assert ran is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "exit_pre_routed_mode" in result.text

    for allowed in ("read_file", "execute", "exit_pre_routed_mode"):
        ran, _ = await _tool_call(middleware, state, allowed)
        assert ran is True, allowed


@pytest.mark.asyncio
async def test_routed_thread_uses_its_route_and_refuses_a_second_exit() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {"pre_routed": False, "model_route": "performance"}

    assert (await _model_call(middleware, state)).model is models["performance"]
    ran, _ = await _tool_call(middleware, state, "edit_file")
    assert ran is True
    ran, result = await _tool_call(middleware, state, "exit_pre_routed_mode")
    assert ran is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


@pytest.mark.asyncio
async def test_stored_route_skips_pre_routed_mode_on_later_runs() -> None:
    middleware, models = _middleware(initial_route="fast")
    state: dict[str, Any] = {"messages": [HumanMessage(content="Follow up")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))

    assert state["pre_routed"] is False
    assert state["model_route"] == "fast"
    assert (await _model_call(middleware, state)).model is models["fast"]


@pytest.mark.asyncio
async def test_route_committed_in_run_state_wins_over_stored_route() -> None:
    middleware, _ = _middleware(initial_route="fast")

    update = await middleware.abefore_agent(cast(Any, {"model_route": "balanced"}), MagicMock())

    assert update == {"model_route": "balanced", "pre_routed": False}


@pytest.mark.asyncio
async def test_plan_mode_uses_performance_and_routes_through_exit_plan_mode() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {"pre_routed": True, "plan_mode": True}

    assert (await _model_call(middleware, state)).model is models["performance"]
    ran, result = await _tool_call(middleware, state, "exit_pre_routed_mode")
    assert ran is False
    assert isinstance(result, ToolMessage)
    assert "exit_plan_mode" in result.text
    ran, _ = await _tool_call(middleware, state, "edit_file")
    assert ran is True


@pytest.mark.asyncio
async def test_unknown_route_falls_back_to_balanced() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {"pre_routed": False, "model_route": "turbo"}

    assert (await _model_call(middleware, state)).model is models["balanced"]
