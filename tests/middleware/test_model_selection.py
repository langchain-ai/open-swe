from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage

from agent.middleware.model_selection import ModelSelectionMiddleware

_TOOLS = [
    {"name": name}
    for name in (
        "exit_pre_routed_mode",
        "execute",
        "read_file",
        "ls",
        "glob",
        "edit_file",
        "write_file",
        "task",
        "open_pull_request",
    )
]


def _middleware(
    initial_route: Any = None,
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock]]:
    models = {profile: MagicMock(name=profile) for profile in ("fast", "balanced", "performance")}
    return ModelSelectionMiddleware(cast(Any, models), initial_route=initial_route), models


async def _invoke(middleware: ModelSelectionMiddleware, state: dict[str, Any]) -> ModelRequest:
    request = ModelRequest(
        model=MagicMock(),
        messages=state["messages"],
        system_message=SystemMessage(content="You are Open SWE."),
        tools=list(_TOOLS),
        state=cast(Any, state),
    )
    seen: list[ModelRequest] = []

    async def handler(routed: ModelRequest) -> ModelResponse:
        seen.append(routed)
        return MagicMock()

    await middleware.awrap_model_call(request, handler)
    return seen[0]


def _names(request: ModelRequest) -> set[str]:
    return {tool["name"] for tool in request.tools}


@pytest.mark.asyncio
async def test_new_thread_starts_pre_routed_on_the_fast_model_with_read_only_tools() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))
    routed = await _invoke(middleware, state)

    assert state == {"messages": state["messages"], "pre_routed": True}
    assert routed.model is models["fast"]
    assert _names(routed) == {"exit_pre_routed_mode", "execute", "read_file", "ls", "glob"}
    assert routed.system_message is not None
    assert routed.system_message.text.startswith("You are Open SWE.")
    assert "Pre-routed Mode (ACTIVE)" in routed.system_message.text


@pytest.mark.asyncio
async def test_exiting_pre_routed_mode_switches_model_and_restores_tools() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="Refactor the auth flow")],
        "pre_routed": False,
        "model_route": "performance",
    }

    routed = await _invoke(middleware, state)

    assert routed.model is models["performance"]
    assert _names(routed) == {tool["name"] for tool in _TOOLS} - {"exit_pre_routed_mode"}
    assert routed.system_message is not None
    assert "Pre-routed Mode" not in routed.system_message.text


@pytest.mark.asyncio
async def test_stored_route_skips_pre_routed_mode_on_later_runs() -> None:
    middleware, models = _middleware(initial_route="fast")
    state: dict[str, Any] = {"messages": [HumanMessage(content="Follow up")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))

    assert state["pre_routed"] is False
    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]


@pytest.mark.asyncio
async def test_route_committed_in_run_state_wins_over_stored_route() -> None:
    middleware, _ = _middleware(initial_route="fast")
    state: dict[str, Any] = {"messages": [], "model_route": "balanced"}

    update = await middleware.abefore_agent(cast(Any, state), MagicMock())

    assert update == {"model_route": "balanced", "pre_routed": False}


@pytest.mark.asyncio
async def test_plan_mode_uses_performance_and_hides_the_routing_tool() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="Plan the migration")],
        "pre_routed": True,
        "plan_mode": True,
    }

    routed = await _invoke(middleware, state)

    assert routed.model is models["performance"]
    assert "exit_pre_routed_mode" not in _names(routed)
    assert "edit_file" in _names(routed)
    assert routed.system_message is not None
    assert "Pre-routed Mode" not in routed.system_message.text


@pytest.mark.asyncio
async def test_unknown_route_falls_back_to_balanced() -> None:
    middleware, models = _middleware()
    state: dict[str, Any] = {"messages": [], "pre_routed": False, "model_route": "turbo"}

    assert (await _invoke(middleware, state)).model is models["balanced"]
