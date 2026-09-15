import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from agent.threads import handlers as thread_api
from agent.threads import routes
from agent.threads.state_view import STUB_KEY, TOOL_OUTPUT_STUB_BYTES, trim_thread_state


def _state(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"values": {"messages": messages}, "next": []}


def test_trim_blanks_large_tool_outputs_and_keeps_the_rest() -> None:
    big = "x" * (TOOL_OUTPUT_STUB_BYTES + 1)
    state = _state(
        [
            {"id": "h1", "type": "human", "content": "hello"},
            {"id": "a1", "type": "ai", "content": "hi", "tool_calls": [{"id": "c1"}]},
            {"id": "t1", "type": "tool", "tool_call_id": "c1", "content": "short"},
            {
                "id": "t2",
                "type": "tool",
                "tool_call_id": "c2",
                "content": big,
                "artifact": {"a": 1},
            },
        ]
    )

    stats = trim_thread_state(state)

    human, ai, small, large = state["values"]["messages"]
    assert human == {"id": "h1", "type": "human", "content": "hello"}
    assert ai["content"] == "hi"
    assert small["content"] == "short"
    assert large["content"] == ""
    assert "artifact" not in large
    assert large["id"] == "t2"
    marker = large["additional_kwargs"][STUB_KEY]
    assert marker["kind"] == "tool_output"
    assert marker["bytes"] > TOOL_OUTPUT_STUB_BYTES
    assert stats == {"stubbed": 1, "saved_bytes": marker["bytes"]}


def test_trim_strips_inline_image_bytes_but_keeps_references() -> None:
    state = _state(
        [
            {
                "id": "h1",
                "type": "human",
                "content": [
                    {"type": "image", "base64": "A" * 100, "mime_type": "image/png"},
                    {"type": "image", "file_id": "f" * 32, "mime_type": "image/png"},
                    {"type": "text", "text": "see"},
                ],
            }
        ]
    )

    stats = trim_thread_state(state)

    inline, reference, text = state["values"]["messages"][0]["content"]
    assert "base64" not in inline
    assert inline[STUB_KEY] == {"kind": "image", "bytes": 100}
    assert reference == {"type": "image", "file_id": "f" * 32, "mime_type": "image/png"}
    assert text == {"type": "text", "text": "see"}
    assert stats == {"stubbed": 1, "saved_bytes": 100}


async def test_state_route_trims_only_when_asked(monkeypatch) -> None:
    thread = {"thread_id": "thread-1", "status": "idle", "metadata": {"source": "dashboard"}}
    big_tool = {
        "id": "t1",
        "type": "tool",
        "tool_call_id": "c1",
        "content": "x" * (TOOL_OUTPUT_STUB_BYTES + 1),
    }
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value=thread),
            get_state=AsyncMock(side_effect=lambda _id: _state([dict(big_tool)])),
        )
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "_assert_thread_readable", lambda *args: None)

    async def refresh(_client, current, *, timings=None):
        return current, "success", "run-1"

    monkeypatch.setattr(thread_api, "_refresh_latest_run_metadata", refresh)

    full = await routes.api_get_thread_state("thread-1", "full", {"sub": "alice"})
    trimmed = await routes.api_get_thread_state("thread-1", "trimmed", {"sub": "alice"})

    assert json.loads(full.body)["values"]["messages"][0]["content"] == big_tool["content"]
    assert "stubbed" not in full.headers["Server-Timing"]
    trimmed_message = json.loads(trimmed.body)["values"]["messages"][0]
    assert trimmed_message["content"] == ""
    assert STUB_KEY in trimmed_message["additional_kwargs"]
    header = trimmed.headers["Server-Timing"]
    assert "trim;dur=" in header
    assert "stubbed;desc=1" in header
