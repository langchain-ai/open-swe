from __future__ import annotations

from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langgraph.errors import InvalidUpdateError

from agent.middleware.invalid_update_guard import (
    GRAPH_UPDATE_FAILURE_MESSAGE,
    InvalidUpdateGuardMiddleware,
)


async def test_guard_converts_invalid_update_error_to_closeout() -> None:
    middleware = InvalidUpdateGuardMiddleware()

    async def handler(_request: object) -> AIMessage:
        raise InvalidUpdateError("At key 'trusted_skills_ref': Can receive only one value")

    with (
        patch(
            "agent.middleware.invalid_update_guard.notify_source_channel",
            new=AsyncMock(return_value=True),
        ) as notify,
        patch(
            "agent.middleware.invalid_update_guard.get_config",
            return_value={"configurable": {}},
        ),
    ):
        result = await middleware.awrap_model_call(object(), handler)

    assert isinstance(result, AIMessage)
    assert result.content == GRAPH_UPDATE_FAILURE_MESSAGE
    notify.assert_awaited_once()


async def test_guard_passes_through_successful_calls() -> None:
    middleware = InvalidUpdateGuardMiddleware()
    expected = AIMessage(content="ok")

    async def handler(_request: object) -> AIMessage:
        return expected

    result = await middleware.awrap_model_call(object(), handler)

    assert result is expected
