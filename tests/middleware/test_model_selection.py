from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from agent.middleware.model_selection import ModelSelectionMiddleware, RouteDecision


def _middleware(
    route: Literal["fast", "balanced", "performance"] = "fast", *, initial_plan_mode: bool = False
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock], AsyncMock]:
    models = {profile: MagicMock(name=profile) for profile in ("fast", "balanced", "performance")}
    structured = AsyncMock(return_value=RouteDecision(model_route=route))
    classifier = MagicMock()
    classifier.with_structured_output.return_value.ainvoke = structured
    return (
        ModelSelectionMiddleware(
            cast(Any, models),
            classifier,
            initial_plan_mode=initial_plan_mode,
        ),
        models,
        structured,
    )


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
async def test_route_is_stored_in_state_and_used_for_model_calls() -> None:
    middleware, models, classifier = _middleware()
    state = {"messages": [HumanMessage(content="Update the README")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))

    assert state["model_route"] == "fast"
    assert (await _invoke(middleware, state)).model is models["fast"]
    classifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_mode_uses_performance_route_without_classifier() -> None:
    middleware, models, classifier = _middleware(initial_plan_mode=True)
    state = {"messages": [HumanMessage(content="Update the docs")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))

    assert state["model_route"] == "performance"
    assert (await _invoke(middleware, state)).model is models["performance"]
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifier_failure_falls_back_to_balanced_route() -> None:
    middleware, models, classifier = _middleware()
    classifier.side_effect = RuntimeError("unavailable")
    state = {"messages": [HumanMessage(content="Do the task")]}

    state.update(await middleware.abefore_agent(cast(Any, state), MagicMock()))

    assert state["model_route"] == "balanced"
    assert (await _invoke(middleware, state)).model is models["balanced"]
