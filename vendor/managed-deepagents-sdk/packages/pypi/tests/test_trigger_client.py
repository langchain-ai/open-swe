"""Trigger callback API bases stay compatible across Cloud and self-hosted."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

import pytest

from managed_deepagents._channels.trigger.client import TriggerClient, create_trigger_client

if TYPE_CHECKING:
    from managed_deepagents._channels.trigger.client import TriggerHttpResponse
    from managed_deepagents._channels.trigger.types import (
        TriggerChannelActionRequest,
        TriggerChannelRuntimeEventRequest,
    )


@pytest.mark.parametrize("key", [None, "preferred-key"])
def test_trigger_client_supports_self_hosted_deployment_key(key: str | None) -> None:
    def fetch(
        _url: str, headers: Mapping[str, str], _body: bytes, _timeout: float
    ) -> TriggerHttpResponse:
        assert headers["x-api-key"] == (
            "preferred-key" if key == "preferred-key" else "deployment-key"
        )
        return {"status": 200, "body": b'{"status":"handled"}'}

    env = {
        "LANGSMITH_MDA_TRIGGER_SERVER_URL": "https://self-hosted.example/api/v1",
        "LANGCHAIN_API_KEY": " deployment-key ",
    }
    if key is not None:
        env["LANGSMITH_API_KEY"] = key
    client = create_trigger_client(env=env, fetch=fetch)
    assert client is not None
    event: TriggerChannelRuntimeEventRequest = {
        "id": "event-1",
        "type": "run.started",
        "delivery_id": "delivery-1",
        "source_thread_key": "slack:v1:A1:T1:C1:171.1",
        "address": {},
        "run_thread_id": "thread-1",
        "run_id": "run-1",
    }
    assert asyncio.run(client.post_runtime_event("channel-1", event)) == {"status": "handled"}


def test_trigger_client_still_requires_canonical_url_and_nonempty_key() -> None:
    assert (
        create_trigger_client(
            env={
                "MDA_TRIGGER_SERVER_URL": "https://legacy.example",
                "LANGCHAIN_API_KEY": "deployment-key",
            }
        )
        is None
    )
    env = {
        "LANGSMITH_MDA_TRIGGER_SERVER_URL": "https://trigger.example",
        "LANGSMITH_API_KEY": " ",
        "LANGCHAIN_API_KEY": " ",
    }
    assert create_trigger_client(env=env) is None
    with pytest.raises(ValueError, match="Channel runtime is not configured"):
        TriggerClient(env=env)


@pytest.mark.parametrize(
    ("configured_base", "api_base"),
    [
        ("https://trigger.example", "https://trigger.example/v1"),
        ("https://trigger.example/", "https://trigger.example/v1"),
        ("https://trigger.example/v1", "https://trigger.example/v1"),
        ("https://trigger.example/v1/", "https://trigger.example/v1"),
        ("https://trigger.example/api/v1", "https://trigger.example/api/v1"),
        ("https://trigger.example/api/v1/", "https://trigger.example/api/v1"),
        ("HTTPS://trigger.example/API/v1", "https://trigger.example/API/v1"),
        (
            "https://trigger.example/langsmith/api/v1",
            "https://trigger.example/langsmith/api/v1",
        ),
        (
            "https://trigger.example/langsmith/api/v1/",
            "https://trigger.example/langsmith/api/v1",
        ),
        (
            "https://trigger.example:8443/teams/acme.dev/api/v1",
            "https://trigger.example:8443/teams/acme.dev/api/v1",
        ),
        ("https://trigger.example/proxy/v1", "https://trigger.example/proxy/v1"),
        (
            "  https://trigger.example/.well-known/api/v1/  ",
            "https://trigger.example/.well-known/api/v1",
        ),
    ],
)
def test_trigger_client_preserves_the_configured_callback_api_base(
    configured_base: str, api_base: str
) -> None:
    calls: list[str] = []
    bodies: list[object] = []
    user_id = "8a10e1ea-42da-4e95-b27f-8f732194ef3e"

    def fetch(
        url: str, headers: Mapping[str, str], body: bytes, _timeout: float
    ) -> TriggerHttpResponse:
        calls.append(url)
        bodies.append(json.loads(body))
        assert headers == {
            "content-type": "application/json",
            "x-api-key": "hosted-server-key",
            "x-user-id": user_id,
        }
        if url.endswith("/events"):
            return {"status": 200, "body": b'{"status":"handled"}'}
        return {"status": 200, "body": b'{"id":"reply-1"}'}

    client = TriggerClient(
        user_id=user_id,
        env={
            "LANGSMITH_MDA_TRIGGER_SERVER_URL": configured_base,
            "LANGSMITH_API_KEY": "hosted-server-key",
        },
        fetch=fetch,
    )
    action: TriggerChannelActionRequest = {
        "type": "post",
        "action_id": "action-1",
        "address": {"route": {"token": "opaque"}},
        "source_thread_key": "slack:v1:A1:T1:C1:171.1",
        "message": {"text": "hello"},
    }
    event: TriggerChannelRuntimeEventRequest = {
        "id": "runtime-event-1",
        "type": "run.started",
        "delivery_id": "delivery-1",
        "source_thread_key": "slack:v1:A1:T1:C1:171.1",
        "address": {"route": {"token": "opaque"}},
        "run_thread_id": "thread-1",
        "run_id": "run-1",
    }

    async def run() -> None:
        assert await client.post_action("channel/1", action) == {"id": "reply-1"}
        assert await client.post_deployment_action("deployment-1", "team/chat", action) == {
            "id": "reply-1"
        }
        assert await client.post_runtime_event("channel/1", event) == {"status": "handled"}

    asyncio.run(run())
    assert calls == [
        f"{api_base}/channels/channel%2F1/actions",
        f"{api_base}/channels/actions?deployment_id=deployment-1&channel_name=team%2Fchat",
        f"{api_base}/channels/channel%2F1/events",
    ]
    assert bodies == [action, action, event]


@pytest.mark.parametrize(
    "url",
    [
        "http://trigger.example",
        "trigger.example",
        "https://user:pass@trigger.example",
        "https://trigger.example/prefix",
        "https://trigger.example/prefix/../v1",
        "https://trigger.example/%2e%2e/v1",
        "https://trigger.example/api/v2",
        "https://trigger.example/api/V1",
        "https://trigger.example/api//v1",
        "https://trigger.example//api/v1",
        "https://trigger.example/api/v1//",
        "https://trigger.example/./api/v1",
        "https://trigger.example/langsmith/../api/v1",
        "https://trigger.example/langsmith/%2e%2e/api/v1",
        "https://trigger.example/langsmith%2Fapi/v1",
        "https://trigger.example/langsmith%5Capi/v1",
        "https://trigger.example/langsmith\\api/v1",
        "https://trigger.example/langsmith/\napi/v1",
        "https://trigger.example/langsmith/\tapi/v1",
        "https://trigger.example/\u212a/api/v1",
        "https://trigger.example/\u0130/api/v1",
        "https://trigger.example/\u0131/api/v1",
        "https://trigger.example:invalid/api/v1",
        "https://trigger.example:99999/api/v1",
        "https://trigger.example/api/v1?",
        "https://trigger.example/api/v1#",
        "https://trigger.example?token=value",
        "https://trigger.example/#fragment",
    ],
)
def test_trigger_client_rejects_an_invalid_callback_api_base(url: str) -> None:
    with pytest.raises(ValueError, match=r"^Channel runtime configuration is invalid\.$"):
        TriggerClient(
            env={
                "LANGSMITH_MDA_TRIGGER_SERVER_URL": url,
                "LANGSMITH_API_KEY": "hosted-server-key",
            }
        )


def test_trigger_client_reads_the_thread_a_top_level_post_started() -> None:
    thread = {
        "channel_id": "channel-1",
        "address": {"conversation_id": "C1", "thread_id": "171.9"},
        "source_thread_key": "slack:v1:A1:T1:C1:171.9",
    }
    responses = [
        {"id": "171.9", **thread},
        {"id": "171.9", "source_thread_key": thread["source_thread_key"]},
    ]

    def fetch(
        _url: str, _headers: Mapping[str, str], _body: bytes, _timeout: float
    ) -> TriggerHttpResponse:
        return {"status": 200, "body": json.dumps(responses.pop(0)).encode()}

    client = TriggerClient(
        env={
            "LANGSMITH_MDA_TRIGGER_SERVER_URL": "https://trigger.example",
            "LANGSMITH_API_KEY": "hosted-server-key",
        },
        fetch=fetch,
    )
    action: TriggerChannelActionRequest = {
        "type": "post",
        "action_id": "action-1",
        "address": {"conversation_id": "C1"},
        "follow_thread": True,
        "message": {"text": "hello"},
    }

    assert asyncio.run(client.post_action("channel-1", action)) == {"id": "171.9", "thread": thread}
    assert asyncio.run(client.post_action("channel-1", action)) == {"id": "171.9"}
