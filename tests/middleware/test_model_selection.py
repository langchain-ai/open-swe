from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.model_selection import (
    ModelSelectionMiddleware,
    RouteDecision,
    RouteSignals,
)


def _decision(route: str = "luna_xhigh") -> RouteDecision:
    return RouteDecision(
        task_category="feature_change",
        initial_route=route,
        signals=RouteSignals(
            scope="localized",
            decomposition="none",
            specification="clear",
            verification="strong",
            risks=[],
            design_needed=False,
            review_needed=False,
        ),
        escalation_conditions=["tests fail unexpectedly"],
        reason="The task is explicit and bounded.",
        confidence=0.9,
    )


def _middleware(
    route: str = "luna_xhigh", *, initial_plan_mode: bool = False
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock], AsyncMock]:
    models = {
        profile: MagicMock(name=profile) for profile in ("luna_xhigh", "terra_high", "sol_medium")
    }
    structured = AsyncMock(return_value=_decision(route))
    classifier = MagicMock()
    classifier.with_structured_output.return_value.ainvoke = structured
    return (
        ModelSelectionMiddleware(
            models,
            classifier,
            initial_plan_mode=initial_plan_mode,
        ),
        models,
        structured,
    )


async def _route(middleware: ModelSelectionMiddleware, state: dict[str, Any]) -> dict[str, Any]:
    update = await middleware.abefore_agent(cast(Any, state), MagicMock())
    if update:
        state.update(update)
    return state


async def _invoke(middleware: ModelSelectionMiddleware, state: dict[str, Any]) -> ModelRequest:
    request = ModelRequest(
        model=MagicMock(),
        messages=state["messages"],
        state=cast(Any, state),
    )
    seen: list[ModelRequest] = []

    async def handler(routed: ModelRequest) -> ModelResponse:
        seen.append(routed)
        return MagicMock()

    await middleware.awrap_model_call(request, handler)
    return seen[0]


@pytest.mark.asyncio
async def test_before_agent_stores_structured_route_plan_in_state() -> None:
    middleware, models, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the README", id="human-1")]}

    await _route(middleware, state)

    assert state["model_route"] == "luna_xhigh"
    assert state["model_route_for"]
    assert state["model_route_plan"]["signals"]["scope"] == "localized"
    assert (await _invoke(middleware, state)).model is models["luna_xhigh"]
    classifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_human_turn_reuses_state_route() -> None:
    middleware, _, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the README", id="human-1")]}
    await _route(middleware, state)
    state["messages"].append(AIMessage(content="Working"))

    update = await middleware.abefore_agent(cast(Any, state), MagicMock())

    assert update is None
    classifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_human_turn_is_reclassified() -> None:
    middleware, _, classifier = _middleware("terra_high")
    state = {"messages": [HumanMessage(content="Add an endpoint", id="human-1")]}
    await _route(middleware, state)
    state["messages"].extend(
        [
            AIMessage(content="Done"),
            HumanMessage(content="Now redesign the database schema", id="human-2"),
        ]
    )

    await _route(middleware, state)

    assert classifier.await_count == 2


@pytest.mark.asyncio
async def test_plan_mode_uses_sol_without_classifier() -> None:
    middleware, models, classifier = _middleware(initial_plan_mode=True)
    state = {"messages": [HumanMessage(content="Update the docs")]}

    await _route(middleware, state)

    assert state["model_route"] == "sol_medium"
    assert state["model_route_plan"] == {}
    assert (await _invoke(middleware, state)).model is models["sol_medium"]
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifier_failure_falls_back_to_terra() -> None:
    middleware, models, classifier = _middleware()
    classifier.side_effect = RuntimeError("unavailable")
    state = {"messages": [HumanMessage(content="Do the task")]}

    await _route(middleware, state)

    assert state["model_route"] == "terra_high"
    assert state["model_route_plan"] == {}
    assert (await _invoke(middleware, state)).model is models["terra_high"]
