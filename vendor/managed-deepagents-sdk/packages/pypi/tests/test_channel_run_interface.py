"""Managed channel context, source authority, and posting contracts."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from managed_deepagents import _ingress
from managed_deepagents._channels import binding as binding_module
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._channels.runtime import create_runtime_channel, json_copy, validate_message
from managed_deepagents._channels.slack.transport import create_slack_web_api_transport
from managed_deepagents._channels.trigger.transport import TriggerTransport

BINDING = {
    "name": "support",
    "provider": "slack",
    "source_thread_key": "source-1",
    "delivery_id": "delivery-1",
    "transport": "trigger_server",
    "event": {"type": "interrupt_resume", "resume": {"i1": True}},
    "target": {
        "channel_id": "channel-1",
        "address": {"opaque": ["a", 1]},
        "source_thread_key": "source-1",
    },
}
AUTH = {"mda_source_provider": "slack", "mda_source_thread_id": "source-1"}


def test_facts_require_matching_current_source_and_channel_name() -> None:
    class Source:
        name = "support"
        kind = "slack"

    register_channel_messaging([Source()])
    resolve = getattr(binding_module, "resolve_channel_info", None)
    assert callable(resolve)
    assert resolve({"channel": BINDING}, {"langgraph_auth_user": AUTH}) is None
    facts = resolve({"mda_channel": BINDING}, {"langgraph_auth_user": AUTH})
    assert facts.name == "support"
    assert facts.event == BINDING["event"]
    assert not hasattr(facts, "post")
    assert (
        resolve(
            {"mda_channel": BINDING},
            {"langgraph_auth_user": {**AUTH, "mda_source_thread_id": "other"}},
        )
        is None
    )
    assert (
        resolve({"mda_channel": {**BINDING, "name": "unknown"}}, {"langgraph_auth_user": AUTH})
        is None
    )
    assert resolve({"mda_channel": BINDING}, {}) is None


def test_post_has_one_message_variant_and_private_target() -> None:
    async def run() -> None:
        sent = []

        class Transport:
            async def post(self, **kwargs: object) -> dict[str, str]:
                sent.append(kwargs)
                return {"id": "p1"}

        target = {"arbitrary": {"nested": [1, 2]}}
        channel = create_runtime_channel(
            name="support",
            provider="slack",
            event={"type": "message", "messages": []},
            target=target,
            transport=Transport(),
        )
        assert not hasattr(channel, "address")
        assert not hasattr(channel, "capabilities")
        assert not hasattr(channel, "update")
        await channel.post({"type": "content", "content": "hello"})
        await channel.post({"type": "native", "native": {"blocks": []}})
        assert sent == [
            {"message": {"type": "content", "content": "hello"}, "target": target},
            {"message": {"type": "native", "native": {"blocks": []}}, "target": target},
        ]
        for message in ({}, {"type": "content", "content": "x", "native": {}}):
            with pytest.raises(ValueError, match="validation error"):
                await channel.post(message)
        with pytest.raises(ValueError, match="validation error"):
            await channel.post({"type": "content", "content": "x", "to": {}})

    asyncio.run(run())


def test_ingress_context_binding_excludes_configurable() -> None:
    build = getattr(_ingress, "build_ingress_run_context", None)
    assert callable(build)
    assert build(
        context={"question": "hello", "channel": "authored"},
        binding=BINDING,
        config={"recursion_limit": 9},
    ) == {
        "context": {
            "question": "hello",
            "channel": "authored",
            "mda_channel": {
                **{
                    key: value
                    for key, value in BINDING.items()
                    if key not in {"source_thread_key", "delivery_id"}
                },
                "sourceThreadKey": BINDING["source_thread_key"],
                "deliveryId": BINDING["delivery_id"],
            },
        },
        "config": {"recursion_limit": 9},
    }
    with pytest.raises(ValueError, match="configurable"):
        build(binding=BINDING, config={"configurable": {"x": 1}})
    with pytest.raises(ValueError, match="reserved"):
        build(context={"mda_channel": {}}, binding=BINDING)


@pytest.mark.parametrize("native", [float("nan"), float("inf"), {"bad": object()}, lambda: None])
def test_rejects_non_json_native(native: object) -> None:
    async def run() -> None:
        class Transport:
            async def post(self, **_kwargs: object) -> dict[str, str]:
                return {"id": "must-not-send"}

        channel = create_runtime_channel(
            name="support",
            provider="slack",
            event={"type": "message", "messages": []},
            target="opaque",
            transport=Transport(),
        )
        with pytest.raises(ValueError, match="JSON"):
            await channel.post({"type": "native", "native": native})

    asyncio.run(run())


@pytest.mark.parametrize("content", [[None], [1], [{"type": "text", "text": object()}]])
def test_rejects_invalid_content_blocks(content: object) -> None:
    async def run() -> None:
        class Transport:
            async def post(self, **_kwargs: object) -> dict[str, str]:
                return {"id": "must-not-send"}

        channel = create_runtime_channel(
            name="support",
            provider="slack",
            event={"type": "message", "messages": []},
            target="opaque",
            transport=Transport(),
        )
        with pytest.raises(ValueError, match="content"):
            await channel.post({"type": "content", "content": content})

    asyncio.run(run())


def test_receive_only_facts_are_immutable_and_invalid_bindings_are_rejected() -> None:

    config = {
        "langgraph_auth_user": AUTH,
        "mda_channel_binding": {
            **BINDING,
            "transport": "direct",
            "target": "opaque",
            "raw_event": {"nested": [1]},
        },
    }
    channel = binding_module.resolve_runtime_channel(
        {"mda_channel": config["mda_channel_binding"]}, config
    )
    assert channel is not None
    assert channel.post is None
    with pytest.raises(FrozenInstanceError):
        channel.name = "other"
    with pytest.raises(TypeError):
        channel.raw_event["nested"] = []
    for change in (
        {"target": float("nan")},
        {"raw_event": {"bad": object()}},
        {"event": {"type": "message", "messages": [False]}},
    ):
        assert (
            binding_module.resolve_channel_info(
                {"mda_channel": {**config["mda_channel_binding"], **change}}, {**config}
            )
            is None
        )


def test_trigger_transport_preserves_route_and_uses_unique_action_ids() -> None:

    async def run() -> None:
        actions: list[tuple[object, object]] = []

        class Client:
            async def post_action(
                self, channel_id: str, action: dict[str, object]
            ) -> dict[str, str]:
                actions.append((channel_id, action))
                return {"id": "posted"}

        transport = TriggerTransport(Client())
        await transport.post(
            message={"type": "content", "content": "one"}, target=BINDING["target"]
        )
        await transport.post(
            message={"type": "content", "content": [{"type": "text", "text": "two"}]},
            target=BINDING["target"],
        )
        assert actions[0][0] == "channel-1"
        assert actions[0][1]["address"] == BINDING["target"]["address"]
        assert actions[0][1]["action_id"] != actions[1][1]["action_id"]
        with pytest.raises(ValueError, match="native"):
            await transport.post(
                message={"type": "native", "native": {"text": "no"}}, target=BINDING["target"]
            )
        with pytest.raises(ValueError, match="text"):
            await transport.post(
                message={"type": "content", "content": [{"type": "image", "url": "x"}]},
                target=BINDING["target"],
            )
        assert len(actions) == 2

    asyncio.run(run())


def test_slack_native_fields_cannot_replace_the_bound_route() -> None:

    bodies: list[object] = []

    def fetch(_url: str, _headers: object, body: bytes) -> dict[str, object]:
        bodies.append(json.loads(body))
        return {"ok": True, "ts": "1"}

    transport = create_slack_web_api_transport(bot_token="test", fetch=fetch)
    asyncio.run(
        transport.post(
            message={
                "type": "native",
                "native": {
                    "text": "hello",
                    "blocks": [],
                    "channel": "evil",
                    "thread_ts": "evil",
                    "token": "evil",
                },
            },
            target={"conversation_id": "C1", "thread_id": "T1"},
        )
    )
    assert bodies == [{"text": "hello", "blocks": [], "channel": "C1", "thread_ts": "T1"}]


def test_binding_accepts_agent_server_proxy_user() -> None:
    class Source:
        name = "support"
        kind = "slack"

    class ProxyUser:
        def __getattr__(self, key: str) -> object:
            try:
                return AUTH[key]
            except KeyError:
                raise AttributeError(key) from None

    register_channel_messaging([Source()])
    facts = binding_module.resolve_channel_info(
        {"mda_channel": BINDING}, {"langgraph_auth_user": ProxyUser()}
    )
    assert facts is not None
    assert facts.name == "support"


def test_post_thaws_frozen_json_and_keeps_target_private() -> None:
    async def run() -> None:
        sent = []

        class Transport:
            async def post(self, **kwargs: object) -> dict[str, str]:
                # Real JSON clients require ordinary dictionaries and arrays.
                sent.append(json.loads(json.dumps(kwargs)))
                kwargs["target"]["ids"].append("changed")
                return {"id": "reply"}

        channel = create_runtime_channel(
            name="support",
            provider="slack",
            event={"type": "message", "messages": []},
            target=MappingProxyType({"ids": ("private",)}),
            raw_event={"blocks": [{"type": "section"}]},
            transport=Transport(),
        )
        assert channel.post is not None
        await channel.post({"type": "native", "native": channel.raw_event})
        await channel.post(
            {"type": "content", "content": (MappingProxyType({"type": "text", "text": "hello"}),)}
        )
        assert sent == [
            {
                "message": {"type": "native", "native": {"blocks": [{"type": "section"}]}},
                "target": {"ids": ["private"]},
            },
            {
                "message": {"type": "content", "content": [{"type": "text", "text": "hello"}]},
                "target": {"ids": ["private"]},
            },
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "message",
    [
        {"content": "missing discriminator"},
        {"type": "native", "content": "mismatch"},
        {"type": "content", "native": None},
        {"type": "content", "content": "hello", "native": {}},
        {"type": "native", "native": {}, "target": {}},
    ],
)
def test_rejects_invalid_message_envelopes(message: object) -> None:

    with pytest.raises(ValueError, match="validation error"):
        validate_message(message)


def test_json_validator_rejects_cycles_and_custom_values() -> None:

    cycle = {}
    cycle["self"] = cycle
    for value in (cycle, object()):
        with pytest.raises(ValueError, match="JSON"):
            validate_message({"type": "native", "native": value})
    native = {"nested": [{"provider_field": "unchanged"}]}
    validate_message({"type": "native", "native": native})
    assert native == {"nested": [{"provider_field": "unchanged"}]}


def test_accepts_typescript_binding_aliases_and_projects_public_events() -> None:
    payload = {"arbitrary": [1, {"provider_field": "keep"}]}
    common = {
        key: value
        for key, value in BINDING.items()
        if key not in {"source_thread_key", "delivery_id"}
    }
    for event in (
        {"type": "message", "messages": [payload]},
        {"type": "interrupt_resume", "resume": payload},
        {
            "type": "user_prompt_response",
            "action": "approve",
            "value": payload,
            "correlation_id": "id",
        },
    ):
        facts = binding_module.resolve_channel_info(
            {
                "mda_channel": {
                    **common,
                    "sourceThreadKey": BINDING["source_thread_key"],
                    "deliveryId": BINDING["delivery_id"],
                    "rawEvent": payload,
                    "event": {**event, "address": {"private": True}, "target": "private"},
                }
            },
            {"langgraph_auth_user": AUTH},
        )
        assert facts is not None

        assert json_copy(facts.event) == event
        assert json_copy(facts.raw_event) == payload
        assert "address" not in facts.event
        assert "target" not in facts.event


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "other"},
        {"source_thread_key": "other"},
        {"name": "unknown"},
        {"delivery_id": " "},
        {"transport": "unknown"},
        {"target": {**BINDING["target"], "channel_id": ""}},
        {"target": {**BINDING["target"], "address": []}},
        {"target": {**BINDING["target"], "source_thread_key": "other"}},
        {"event": {"type": "user_prompt_response", "action": "approve", "correlation_id": None}},
    ],
)
def test_rejects_malformed_or_untrusted_bindings(change: object) -> None:
    assert (
        binding_module.resolve_channel_info(
            {"mda_channel": {**BINDING, **change}}, {"langgraph_auth_user": AUTH}
        )
        is None
    )


def test_accepts_frozen_message_envelopes_and_binding_events() -> None:
    native = MappingProxyType({"items": (1, 2)})
    validate_message(MappingProxyType({"type": "native", "native": native}))
    validate_message(
        MappingProxyType(
            {"type": "content", "content": (MappingProxyType({"type": "text", "text": "hello"}),)}
        )
    )
    facts = binding_module.resolve_channel_info(
        {
            "mda_channel": MappingProxyType(
                {
                    **BINDING,
                    "target": MappingProxyType(BINDING["target"]),
                    "event": MappingProxyType({"type": "message", "messages": (native,)}),
                }
            )
        },
        {"langgraph_auth_user": AUTH},
    )
    assert facts is not None
    assert facts.event["messages"] == (native,)
