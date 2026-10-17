"""Unit tests for schedule deliver_to auto-post."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from managed_deepagents._channels import binding
from managed_deepagents._channels.http import http_channel
from managed_deepagents._channels.schedule_delivery import (
    address_from_deliver_to,
    create_schedule_delivery_middleware,
    deliver_schedule_result,
    get_deployment_id,
    read_schedule_deliver_to,
    set_schedule_metadata_for_tests,
)
from managed_deepagents._channels.trigger import client as trigger_client
from managed_deepagents._run_input import execution_context

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


SCHEDULED = {"langgraph_auth_user": {"mda_scheduled_run": True}}
DELIVER_TO = {"channel": "slack", "to": {"type": "provider_conversation", "conversation_id": "C9"}}


def _run_scheduled(
    deliver: Callable[..., Awaitable[object]],
    routing: Mapping[str, object] | None,
    runtime: object,
) -> None:
    mw = create_schedule_delivery_middleware(deliver=deliver)
    token = binding._current_routing.set(routing)
    try:
        asyncio.run(
            mw.aafter_agent({"messages": [{"type": "ai", "content": "Happy Monday!"}]}, runtime)
        )
    finally:
        binding._current_routing.reset(token)
        set_schedule_metadata_for_tests()


def test_middleware_delivers_after_agent_for_trusted_cron() -> None:
    calls: list[dict[str, Any]] = []

    async def deliver(**kwargs: Any) -> str:
        calls.append(kwargs)
        return "posted"

    set_schedule_metadata_for_tests(lambda: {"mda_managed": True})
    _run_scheduled(
        deliver,
        {"mda_schedule": {"name": "monday-greeting", "deliver_to": DELIVER_TO}},
        {"configurable": SCHEDULED},
    )
    assert calls == [
        {"deliver_to": {**DELIVER_TO, "auto_post": True}, "reply_text": "Happy Monday!"}
    ]


def test_middleware_ignores_forged_http_deliver_to() -> None:
    calls: list[dict[str, Any]] = []

    async def deliver(**kwargs: Any) -> str:
        calls.append(kwargs)
        return "posted"

    set_schedule_metadata_for_tests(lambda: {"mda_managed": True})
    _run_scheduled(
        deliver,
        {"mda_schedule": {"name": "forged", "deliver_to": DELIVER_TO}},
        {
            "configurable": {
                "mda_schedule": {"name": "forged", "deliver_to": DELIVER_TO},
                "langgraph_auth_user": {"identity": "caller"},
            }
        },
    )
    assert calls == []


def test_middleware_reads_configurable_from_ambient_get_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle hooks often omit runtime.configurable; use ambient LangGraph config."""
    calls: list[dict[str, Any]] = []

    async def deliver(**kwargs: Any) -> str:
        calls.append(kwargs)
        return "posted"

    set_schedule_metadata_for_tests(lambda: {"mda_managed": True})
    monkeypatch.setattr(
        "managed_deepagents._identity_runtime.get_config", lambda: {"configurable": SCHEDULED}
    )
    _run_scheduled(deliver, {"mda_schedule": {"deliver_to": DELIVER_TO}}, None)
    assert calls == [
        {"deliver_to": {**DELIVER_TO, "auto_post": True}, "reply_text": "Happy Monday!"}
    ]


@pytest.mark.parametrize(
    ("metadata", "routing"),
    [
        pytest.param({}, {"mda_schedule": {"deliver_to": DELIVER_TO}}, id="no-cron-metadata"),
        pytest.param({"mda_managed": True}, {"mda_schedule": {"name": "x"}}, id="no-deliver-to"),
    ],
)
def test_middleware_no_ops(metadata: dict[str, object], routing: dict[str, object]) -> None:
    calls: list[dict[str, Any]] = []

    async def deliver(**kwargs: Any) -> str:
        calls.append(kwargs)
        return "posted"

    set_schedule_metadata_for_tests(lambda: metadata)
    _run_scheduled(deliver, routing, {"configurable": SCHEDULED})
    assert calls == []


def test_middleware_is_agent_middleware_subclass_when_langchain_present() -> None:
    """create_agent inspects AgentMiddleware class attrs; duck types crash compile."""
    middleware_types = pytest.importorskip("langchain.agents.middleware.types")
    mw = create_schedule_delivery_middleware()
    assert isinstance(mw, middleware_types.AgentMiddleware)
    assert mw.name == "mda-schedule-delivery"
    assert type(mw).after_agent is not middleware_types.AgentMiddleware.after_agent
    assert type(mw).aafter_agent is not middleware_types.AgentMiddleware.aafter_agent


def test_middleware_compiles_with_create_deep_agent() -> None:
    """Regression: duck-typed middleware raised AttributeError in create_agent."""
    deepagents = pytest.importorskip("deepagents")
    chat_models = pytest.importorskip("langchain_core.language_models.chat_models")
    messages = pytest.importorskip("langchain_core.messages")
    outputs = pytest.importorskip("langchain_core.outputs")

    class _FakeModel(chat_models.BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "fake"

        def _generate(
            self,
            messages_: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> Any:
            return outputs.ChatResult(
                generations=[outputs.ChatGeneration(message=messages.AIMessage(content="PONG"))]
            )

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

    mw = create_schedule_delivery_middleware()
    agent = deepagents.create_deep_agent(model=_FakeModel(), middleware=[mw], tools=[])
    result = agent.invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert result["messages"][-1].content == "PONG"


@pytest.mark.parametrize("new_thread", [False, True])
@pytest.mark.parametrize("conversation", ["C123", "D123"])
@pytest.mark.parametrize("legacy", [False, True])
def test_automatic_slack_delivery_uses_captured_target(
    monkeypatch: pytest.MonkeyPatch, new_thread: bool, conversation: str, legacy: bool
) -> None:
    post = AsyncMock(return_value={"id": "posted"})
    monkeypatch.setattr(
        trigger_client, "create_trigger_client", lambda: SimpleNamespace(post_action=post)
    )
    monkeypatch.setattr(binding, "channel_provider_for_name", lambda _: "slack")
    channel = {
        "name": "team",
        "provider": "slack",
        "transport": "trigger_server",
        "source_thread_key": "source",
        "delivery_id": "origin",
        "target": {
            "channel_id": "install",
            "source_thread_key": "source",
            "address": {"conversation_id": conversation, "thread_id": "123.456"},
        },
        "event": {"type": "schedule", "new_thread": new_thread},
    }
    config = {
        "langgraph_auth_user": {
            "mda_scheduled_run": True,
            "mda_source_provider": "slack",
            "mda_source_thread_id": "source",
        },
    }
    set_schedule_metadata_for_tests(lambda: {"mda_managed": True})
    _, _, routing = execution_context(
        None if legacy else {"mda_channel": channel},
        {
            "configurable": {
                **config,
                **({"mda_schedule": {"name": "daily"}, "channel": channel} if legacy else {}),
            }
        },
        None,
    )
    token = binding._current_routing.set(routing)
    try:
        middleware = create_schedule_delivery_middleware()
        for _ in range(2):
            asyncio.run(
                middleware.aafter_agent(
                    {"messages": [{"type": "ai", "content": "Hello"}]},
                    SimpleNamespace(configurable=config),
                )
            )
        assert post.await_count == 2
        action = post.await_args.args[1]
        assert post.await_args.args[0] == "install"
        assert action["address"] == {
            "conversation_id": conversation,
            **({} if new_thread else {"thread_id": "123.456"}),
        }
        assert ("source_thread_key" in action) is not new_thread
        assert channel["target"]["address"]["thread_id"] == "123.456"
        assert action["message"] == {"text": "Hello"}
    finally:
        binding._current_routing.reset(token)
        set_schedule_metadata_for_tests()


@pytest.mark.parametrize("new_thread", [False, True])
def test_automatic_http_delivery_uses_existing_post_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch, new_thread: bool
) -> None:
    post = AsyncMock(return_value={"id": "posted"})
    adapter = http_channel(
        provider="custom", verify=lambda _: True, parse=lambda _: {"type": "ignore"}, post=post
    )
    monkeypatch.setattr(binding, "channel_provider_for_name", lambda _: "custom")
    monkeypatch.setattr(binding, "messaging_transport_for_name", adapter._build_messaging)
    channel = {
        "name": "updates",
        "provider": "custom",
        "transport": "direct",
        "source_thread_key": "source",
        "delivery_id": "origin",
        "target": {"room": "team", "opaque": [1, 2]},
        "event": {"type": "schedule", "new_thread": new_thread},
    }
    config = {
        "langgraph_auth_user": {
            "mda_scheduled_run": True,
            "mda_source_provider": "custom",
            "mda_source_thread_id": "source",
        },
    }
    state = {"messages": [{"type": "ai", "content": "Hello"}]}
    runtime = SimpleNamespace(configurable=config)
    set_schedule_metadata_for_tests(lambda: {"mda_managed": True})
    token = binding._current_routing.set({"mda_channel": channel})
    try:
        middleware = create_schedule_delivery_middleware()
        asyncio.run(middleware.aafter_agent(state, runtime))
        post.assert_awaited_once_with(
            {
                "type": "content",
                "content": "Hello",
                "target": channel["target"],
                "new_thread": new_thread,
            }
        )
        blocks = [{"type": "image", "url": "https://example.test/report.png"}]
        asyncio.run(
            middleware.aafter_agent({"messages": [{"type": "ai", "content": blocks}]}, runtime)
        )
        assert post.await_args.args[0]["content"] == blocks
        post.side_effect = RuntimeError("provider unavailable")
        with pytest.raises(RuntimeError, match="provider unavailable"):
            asyncio.run(middleware.aafter_agent(state, runtime))
        config["langgraph_auth_user"]["mda_source_thread_id"] = "forged"
        with pytest.raises(RuntimeError, match="unavailable"):
            asyncio.run(middleware.aafter_agent(state, runtime))
        set_schedule_metadata_for_tests(dict)
        post.reset_mock()
        asyncio.run(middleware.aafter_agent(state, runtime))
        post.assert_not_awaited()
    finally:
        binding._current_routing.reset(token)
        set_schedule_metadata_for_tests()
