"""Request assembly and error handling in the Linear GraphQL client."""

import json
from typing import Any

import httpx2
import pytest

from agent.linear.client import LinearClient, LinearError
from agent.linear.schema import ActionContent, ThoughtContent


class _Recorder:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json=self.response)

    @property
    def variables(self) -> dict[str, Any]:
        return self.requests[-1]["variables"]


def _client(handler: _Recorder) -> LinearClient:
    client = LinearClient()
    client._http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return client


async def test_agent_activity_input_omits_unset_modifiers() -> None:
    handler = _Recorder({"data": {"agentActivityCreate": {"success": True}}})

    await _client(handler).create_agent_activity(
        "session-1", ThoughtContent(body="reading the failing test")
    )

    assert handler.variables["input"] == {
        "agentSessionId": "session-1",
        "content": {"type": "thought", "body": "reading the failing test"},
    }


async def test_agent_activity_input_carries_ephemeral_and_signal() -> None:
    handler = _Recorder({"data": {"agentActivityCreate": {"success": True}}})

    await _client(handler).create_agent_activity(
        "session-1",
        ActionContent(action="Reading", parameter="agent/server.py"),
        ephemeral=True,
        signal="stop",
    )

    assert handler.variables["input"] == {
        "agentSessionId": "session-1",
        "content": {"type": "action", "action": "Reading", "parameter": "agent/server.py"},
        "ephemeral": True,
        "signal": "stop",
    }


async def test_external_url_update_sends_the_session_id_and_link() -> None:
    handler = _Recorder({"data": {"agentSessionUpdateExternalUrl": {"success": True}}})

    await _client(handler).update_agent_session_external_url("session-1", "https://smith/t/1")

    assert handler.variables == {"id": "session-1", "url": "https://smith/t/1"}


async def test_graphql_errors_raise() -> None:
    handler = _Recorder({"errors": [{"message": "Entity not found"}]})

    with pytest.raises(LinearError) as raised:
        await _client(handler).get_issue("missing")

    assert raised.value.errors == [{"message": "Entity not found"}]


async def test_viewer_id_is_fetched_once() -> None:
    handler = _Recorder({"data": {"viewer": {"id": "app-1"}}})
    client = _client(handler)

    assert await client.viewer_id() == "app-1"
    assert await client.viewer_id() == "app-1"
    assert len(handler.requests) == 1
