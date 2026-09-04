from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.model_selection import ModelSelectionMiddleware, RouteDecision


def _request(prompt: str, *messages: Any) -> ModelRequest[None]:
    return ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content=prompt), *messages],
        state=cast(Any, {}),
    )


def _middleware(
    complexity: str = "small", *, initial_plan_mode: bool = False
) -> tuple[ModelSelectionMiddleware, dict[str, MagicMock], AsyncMock]:
    models = {tier: MagicMock(name=tier) for tier in ("small", "medium", "complex")}
    structured = AsyncMock(return_value=RouteDecision(complexity=complexity))
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


async def _invoke(middleware: ModelSelectionMiddleware, request: ModelRequest) -> ModelRequest:
    seen: list[ModelRequest] = []

    async def handler(routed: ModelRequest) -> ModelResponse:
        seen.append(routed)
        return MagicMock()

    await middleware.awrap_model_call(request, handler)
    return seen[0]


@pytest.mark.asyncio
async def test_middleware_uses_classifier_route_for_entire_turn() -> None:
    middleware, models, classifier = _middleware("small")
    first = _request("Update the README")
    followup = _request("Update the README", AIMessage(content="Working"))

    assert (await _invoke(middleware, first)).model is models["small"]
    assert (await _invoke(middleware, followup)).model is models["small"]
    classifier.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_human_turn_is_reclassified() -> None:
    middleware, _, classifier = _middleware("medium")

    await _invoke(middleware, _request("Add an endpoint"))
    await _invoke(
        middleware,
        _request(
            "Add an endpoint",
            AIMessage(content="Done"),
            HumanMessage(content="Now redesign the database schema"),
        ),
    )

    assert classifier.await_count == 2


@pytest.mark.asyncio
async def test_plan_mode_uses_complex_route_without_classifier() -> None:
    middleware, models, classifier = _middleware(initial_plan_mode=True)

    routed = await _invoke(middleware, _request("Update the docs"))

    assert routed.model is models["complex"]
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifier_failure_falls_back_to_medium() -> None:
    middleware, models, classifier = _middleware()
    classifier.side_effect = RuntimeError("unavailable")

    routed = await _invoke(middleware, _request("Do the task"))

    assert routed.model is models["medium"]
