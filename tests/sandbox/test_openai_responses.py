"""The sandbox Responses endpoint: transcript projection and thread continuation."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from openswe.dashboard.workspace_settings import WorkspaceSettingsUpdate, upsert_workspace_overrides
from openswe.openai_responses import conversations, routes
from openswe.openai_responses.conversations import SandboxCaller
from openswe.openai_responses.ids import OpenSweId
from openswe.openai_responses.models import (
    ConversationRef,
    CreateResponseRequest,
    FunctionCallItem,
    McpCallItem,
    MessageItem,
    Response,
    WebSearchCallItem,
)
from openswe.openai_responses.projection import ResponseProjection
from openswe.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY
from openswe.transcript.events import (
    MessageAppended,
    MessageCompleted,
    MessageUsage,
    StoredEvent,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnStarted,
)
from tests.conftest import FakeStore

THREAD = str(uuid.uuid4())
RUN = str(uuid.uuid4())


async def test_api_flag_blocks_requests_and_can_be_revoked(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = SandboxCaller("host-thread", "sandbox-a", {"workspace": "experiment"})
    monkeypatch.setattr(SandboxCaller, "authenticate", AsyncMock(return_value=caller))
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://agent.example.test"
    ) as client:
        for method, path, body in (
            ("GET", "/models", None),
            ("POST", "/responses", {"input": "hello"}),
            ("GET", "/responses/unknown", None),
            ("POST", "/responses/unknown/cancel", None),
        ):
            response = await client.request(method, routes.OPENAI_PATH + path, json=body)
            assert response.status_code == 403
        await upsert_workspace_overrides(
            "experiment", WorkspaceSettingsUpdate(sandbox_openai_enabled=True)
        )
        assert (await client.get(routes.OPENAI_PATH + "/models")).status_code == 200
        caller.host_metadata["workspace"] = "other"
        assert (await client.get(routes.OPENAI_PATH + "/models")).status_code == 403
        caller.host_metadata["workspace"] = "experiment"
        await upsert_workspace_overrides(
            "experiment", WorkspaceSettingsUpdate(sandbox_openai_enabled=False)
        )
        assert (await client.get(routes.OPENAI_PATH + "/models")).status_code == 403


def stored(version: int, body: BaseModel) -> StoredEvent:
    return StoredEvent(
        thread_id=THREAD,
        version=version,
        event_id=uuid.uuid4(),
        event_type=str(body.model_dump()["type"]),
        schema_version=1,
        run_id=RUN,
        turn_id=None,
        command_id=None,
        actor_kind="agent",
        occurred_at=datetime.now(UTC),
        payload=body.model_dump(mode="json"),
    )


@pytest.mark.parametrize("codex", [False, True])
async def test_projection_streams_text_and_executed_tool_calls(codex: bool) -> None:
    ids = OpenSweId(THREAD, RUN)
    turn, other_turn = uuid.uuid4(), uuid.uuid4()
    response = Response(
        id=ids.response_id(),
        created_at=0,
        status="queued",
        model="open-swe",
        conversation=ConversationRef(id=THREAD),
    )
    projection = ResponseProjection(
        response, ids, web_search_tools=codex, client_tools={"exec_command": "function"}
    )
    bodies: list[BaseModel] = [
        MessageAppended(turn_id=turn, message_id="early", text="before the run"),
        TurnStarted(turn_id=turn, run_id=RUN),
        MessageAppended(turn_id=turn, message_id="ai-1", text=" Hel"),
        ToolStarted(turn_id=turn, tool_call_id="call-1", name="execute", input={"command": "ls"}),
        ToolStarted(turn_id=turn, tool_call_id="sub-1", name="read_file", namespace=["task:1"]),
        MessageAppended(turn_id=other_turn, message_id="ai-9", text="other turn"),
        ToolCompleted(turn_id=turn, tool_call_id="call-1", status="completed", output_preview="a"),
        MessageCompleted(
            turn_id=turn,
            message_id="ai-1",
            role="ai",
            text="Hello",
            usage=MessageUsage(input_tokens=100, output_tokens=20),
            created_at=datetime.now(UTC),
        ),
        MessageCompleted(
            turn_id=turn,
            message_id="ai-2",
            role="ai",
            usage=MessageUsage(input_tokens=150, output_tokens=30),
            created_at=datetime.now(UTC),
        ),
        ToolStarted(turn_id=turn, tool_call_id="call-2", name="exec_command", input={"cmd": "pwd"}),
        ToolCompleted(turn_id=turn, tool_call_id="call-2", status="completed", output_preview="-"),
        TurnCompleted(turn_id=turn, run_id=RUN),
    ]
    events = projection.start()
    for version, body in enumerate(bodies, start=1):
        events.extend(await projection.apply(stored(version, body)))

    assert response.status == "completed"
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.total_tokens) == (250, 300)
    message, tool, client_call = response.output
    assert isinstance(client_call, FunctionCallItem)
    assert (client_call.call_id, client_call.name, client_call.arguments) == (
        "call-2",
        "exec_command",
        '{"cmd": "pwd"}',
    )
    assert isinstance(message, MessageItem)
    assert (message.content[0].text, message.status) == ("Hello", "completed")
    if codex:
        assert isinstance(tool, WebSearchCallItem)
        assert (tool.action.query, tool.status) == ("oswe_execute: ls", "completed")
    else:
        assert isinstance(tool, McpCallItem)
        assert (tool.name, tool.arguments, tool.output, tool.status) == (
            "oswe_execute",
            '{"command": "ls"}',
            "a",
            "completed",
        )
    assert [e.delta for e in events if e.type == "response.output_text.delta"] == [" Hel", "lo"]
    added = next(e for e in events if e.type == "response.output_item.added")
    assert isinstance(added.item, MessageItem) and added.item.content == []
    assert events[-1].type == "response.completed"
    assert [e.sequence_number for e in events] == list(range(len(events)))
    assert OpenSweId.parse(message.id) == OpenSweId(THREAD)


async def test_echoed_ids_continue_only_threads_this_sandbox_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own, foreign = str(uuid.uuid4()), str(uuid.uuid4())
    hosts = {own: "host-thread", foreign: "another-host"}
    client = MagicMock()
    client.threads.get = AsyncMock(
        side_effect=lambda thread_id: {"metadata": {SANDBOX_HOST_THREAD_KEY: hosts[thread_id]}}
    )
    monkeypatch.setattr(conversations, "langgraph_client", lambda: client)
    caller = SandboxCaller("host-thread", "sandbox-a", {"owner_login": "octocat"})

    def request(thread_id: str) -> CreateResponseRequest:
        return CreateResponseRequest.model_validate(
            {
                "model": "gpt-5",
                "input": [
                    {"role": "developer", "content": "client harness prompt"},
                    {"role": "user", "content": "first"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "id": OpenSweId(thread_id).item_id("msg", "ai-1"),
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
                ],
                "tools": [{"type": "function", "name": "shell"}],
            }
        )

    continuation = await caller.resolve(request(own))
    assert continuation.thread_id == own
    assert [item.text() for item in continuation.items] == ["next"]
    with pytest.raises(HTTPException) as refused:
        await caller.resolve(request(foreign))
    assert refused.value.status_code == 404
