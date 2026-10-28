"""Unit tests for schedule deliver_to auto-post."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from managed_deepagents._channels.schedule_delivery import (
    address_from_deliver_to,
    deliver_schedule_result,
    get_deployment_id,
    is_trusted_schedule_delivery_run,
    read_schedule_deliver_to,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.trigger.types import (
        TriggerChannelActionRequest,
        TriggerChannelActionResponse,
    )


def test_read_camel_case_ts_shape() -> None:
    assert read_schedule_deliver_to(
        {
            "channel": "slack",
            "to": {
                "type": "provider_conversation",
                "conversationId": "C123",
            },
            "autoPost": True,
        }
    ) == {
        "channel": "slack",
        "to": {"type": "provider_conversation", "conversation_id": "C123"},
        "auto_post": True,
    }


def test_read_snake_case_python_shape() -> None:
    assert read_schedule_deliver_to(
        {
            "channel": "slack",
            "to": {
                "type": "provider_thread",
                "conversation_id": "C123",
                "thread_id": "111.222",
            },
            "auto_post": False,
        }
    ) == {
        "channel": "slack",
        "to": {
            "type": "provider_thread",
            "conversation_id": "C123",
            "thread_id": "111.222",
        },
        "auto_post": False,
    }


def test_address_from_deliver_to() -> None:
    assert address_from_deliver_to({"type": "provider_conversation", "conversation_id": "C1"}) == {
        "conversation_id": "C1"
    }
    assert address_from_deliver_to(
        {
            "type": "provider_thread",
            "conversation_id": "C1",
            "thread_id": "t1",
        }
    ) == {"conversation_id": "C1", "thread_id": "t1"}


def test_get_deployment_id_trims_each_candidate_before_fallback() -> None:
    assert (
        get_deployment_id(
            {
                "LANGSMITH_HOST_PROJECT_ID": "  ",
                "LANGGRAPH_CLOUD_ID": " deployment-1 ",
            }
        )
        == "deployment-1"
    )
    assert (
        get_deployment_id(
            {
                "LANGSMITH_HOST_PROJECT_ID": " host-1 ",
                "LANGGRAPH_CLOUD_ID": "cloud-1",
            }
        )
        == "host-1"
    )
    assert get_deployment_id({}) is None


def test_deliver_posts_when_autopost_true() -> None:
    calls: list[dict[str, Any]] = []

    async def post(**kwargs: Any) -> None:
        calls.append(kwargs)

    async def _run() -> None:
        outcome = await deliver_schedule_result(
            deliver_to={
                "channel": "slack",
                "to": {"type": "provider_conversation", "conversation_id": "C1"},
                "auto_post": True,
            },
            reply_text="Hello Monday",
            post=post,
        )
        assert outcome == "posted"
        assert calls == [
            {
                "text": "Hello Monday",
                "conversation_id": "C1",
                "thread_id": None,
            }
        ]

    asyncio.run(_run())


def test_deliver_skips_when_autopost_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    calls: list[object] = []

    async def post(**kwargs: object) -> None:
        calls.append(kwargs)

    async def _run() -> None:
        assert (
            await deliver_schedule_result(
                deliver_to={
                    "channel": "slack",
                    "to": {"type": "provider_conversation", "conversation_id": "C1"},
                    "auto_post": False,
                },
                reply_text="Hello",
                post=post,
            )
            == "skipped"
        )
        assert calls == []

    asyncio.run(_run())


def test_deliver_soft_fails_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LANGSMITH_HOST_PROJECT_ID", raising=False)
    monkeypatch.delenv("LANGGRAPH_CLOUD_ID", raising=False)
    monkeypatch.setattr(
        "managed_deepagents._channels.schedule_delivery.create_trigger_client",
        lambda: None,
    )

    async def _run() -> None:
        assert (
            await deliver_schedule_result(
                deliver_to={
                    "channel": "slack",
                    "to": {"type": "provider_conversation", "conversation_id": "C1"},
                    "auto_post": True,
                },
                reply_text="Hello",
            )
            == "failed"
        )

    asyncio.run(_run())


class _ScheduleActionClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, TriggerChannelActionRequest]] = []

    async def post_deployment_action(
        self,
        deployment_id: str,
        channel_name: str,
        body: TriggerChannelActionRequest,
    ) -> TriggerChannelActionResponse:
        if self.fail:
            raise RuntimeError("unavailable")
        self.calls.append((deployment_id, channel_name, body))
        return {"id": "message-1"}


def test_deliver_posts_through_trigger_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _ScheduleActionClient()
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "deployment-1")
    monkeypatch.setattr(
        "managed_deepagents._channels.schedule_delivery.create_trigger_client",
        lambda: client,
    )

    async def _run() -> None:
        assert (
            await deliver_schedule_result(
                deliver_to={
                    "channel": "slack",
                    "to": {
                        "type": "provider_thread",
                        "conversation_id": "C1",
                        "thread_id": "171.1",
                    },
                    "auto_post": True,
                },
                reply_text="Hello",
            )
            == "posted"
        )

    asyncio.run(_run())
    assert len(client.calls) == 1
    deployment_id, channel_name, action = client.calls[0]
    assert (deployment_id, channel_name) == ("deployment-1", "slack")
    assert action["address"] == {
        "conversation_id": "C1",
        "thread_id": "171.1",
    }
    assert action["message"] == {"text": "Hello"}
    assert "source_thread_key" not in action


def test_deliver_does_not_fall_back_when_trigger_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "deployment-1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "unused-placeholder")
    monkeypatch.setattr(
        "managed_deepagents._channels.schedule_delivery.create_trigger_client",
        lambda: _ScheduleActionClient(fail=True),
    )

    async def _run() -> None:
        assert (
            await deliver_schedule_result(
                deliver_to={
                    "channel": "slack",
                    "to": {"type": "provider_conversation", "conversation_id": "C1"},
                    "auto_post": True,
                },
                reply_text="Hello",
            )
            == "failed"
        )

    asyncio.run(_run())


def test_a_trusted_schedule_delivery_run_needs_the_cron_principal_and_managed_metadata() -> None:
    scheduled = {"langgraph_auth_user": {"mda_scheduled_run": True}}
    assert is_trusted_schedule_delivery_run(scheduled, {"mda_managed": True})
    assert not is_trusted_schedule_delivery_run(scheduled, {})
    assert not is_trusted_schedule_delivery_run(
        {"langgraph_auth_user": {"identity": "caller"}}, {"mda_managed": True}
    )
