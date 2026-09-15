import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import patch_thread_module
from fastapi import HTTPException

from agent.threads import message_fetch, routes

MESSAGES: list[dict[str, Any]] = [
    {"id": "h1", "type": "human", "content": "hello"},
    {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "x" * 10_000},
]


def _client(messages: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        threads=SimpleNamespace(
            get_state=AsyncMock(return_value={"values": {"messages": messages}})
        )
    )


async def test_returns_the_full_message_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_thread_module(monkeypatch, "_readable_thread_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(message_fetch, "langgraph_client", lambda: _client(MESSAGES))

    response = await routes.api_get_thread_message("thread-1", "t1", {"sub": "alice"})

    assert json.loads(response.body) == MESSAGES[1]
    phases = {part.split(";", 1)[0] for part in response.headers["Server-Timing"].split(", ")}
    assert phases == {"thread_get", "get_state", "total"}


@pytest.mark.parametrize("message_id", ["missing", "", "m" * 201])
async def test_unknown_messages_are_not_found(
    monkeypatch: pytest.MonkeyPatch, message_id: str
) -> None:
    patch_thread_module(monkeypatch, "_readable_thread_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(message_fetch, "langgraph_client", lambda: _client(MESSAGES))

    with pytest.raises(HTTPException) as exc:
        await message_fetch.get_dashboard_thread_message("thread-1", message_id, "alice")
    assert exc.value.status_code == 404


async def test_unreadable_threads_do_not_read_state(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_thread_module(
        monkeypatch,
        "_readable_thread_metadata",
        AsyncMock(side_effect=HTTPException(404, "thread not found")),
    )
    client = _client(MESSAGES)
    monkeypatch.setattr(message_fetch, "langgraph_client", lambda: client)

    with pytest.raises(HTTPException):
        await message_fetch.get_dashboard_thread_message("thread-1", "h1", "mallory")
    client.threads.get_state.assert_not_awaited()
