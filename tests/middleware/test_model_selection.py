from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from agent.middleware.model_selection import ModelSelectionMiddleware, task_complexity


def _request(prompt: str, *, plan_mode: bool = False) -> ModelRequest[None]:
    return ModelRequest(
        model=MagicMock(),
        messages=[HumanMessage(content=prompt)],
        state=cast(Any, {"plan_mode": plan_mode}),
    )


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Fix this typo in the README", "small"),
        ("Add an endpoint that returns the current version", "medium"),
        ("Plan a database migration across multiple services", "complex"),
    ],
)
def test_task_complexity(prompt: str, expected: str) -> None:
    assert task_complexity(_request(prompt)) == expected


def test_plan_mode_uses_complex_tier() -> None:
    assert task_complexity(_request("Update the docs", plan_mode=True)) == "complex"


@pytest.mark.asyncio
async def test_middleware_overrides_model_for_selected_tier() -> None:
    models = {tier: MagicMock(name=tier) for tier in ("small", "medium", "complex")}
    middleware = ModelSelectionMiddleware(models)
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return MagicMock()

    await middleware.awrap_model_call(_request("Fix this typo"), handler)

    assert seen[0].model is models["small"]
