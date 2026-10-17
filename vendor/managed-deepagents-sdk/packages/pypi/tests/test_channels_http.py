from __future__ import annotations

import asyncio
import hmac
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from managed_deepagents import HttpChannelParseResult, HttpChannelRequest, _ingress, channels
from managed_deepagents._channel import collect_channels, requirements_from_channels
from managed_deepagents._channels.binding import resolve_runtime_channel
from managed_deepagents._channels.http import ingress as http_ingress
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._identity_http import build_managed_http_app
from tests.test_identity_http import _call

if TYPE_CHECKING:
    from managed_deepagents._channels.http import HttpChannel


def message() -> dict[str, object]:
    return {
        "user_id": "photon:line-1:user-1",
        "thread_id": "11111111-1111-4111-8111-111111111111",
        "content": "hello",
        "target": {"conversation_id": "provider-conversation"},
    }


def context(channel: object, raw: bytes = b'{"order":"one"}') -> dict[str, object]:
    return {
        "name": "imessage",
        "channel": channel,
        "request": Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/channels/imessage/events",
                "headers": [(b"x-event", b"delivery-1")],
            }
        ),
        "raw_body": raw,
    }


def make_channel(**kwargs: object) -> HttpChannel:
    return channels.http(
        **{
            "provider": "orders",
            "verify": lambda ctx: True,
            "parse": lambda ctx: {"type": "message", "message": message()},
            **kwargs,
        }
    )


@pytest.fixture
def ingress(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("MDA_ASSISTANT_ID", "agent")
    start = AsyncMock(return_value={"thread_id": "thread-1", "run_id": "run-1"})
    observe = AsyncMock(return_value={"reply_content": "Hello from the agent"})
    monkeypatch.setattr(http_ingress, "start_http_ingress_agent", start)
    monkeypatch.setattr(http_ingress, "observe_http_ingress_agent", observe)
    jobs = []
    monkeypatch.setattr(_ingress, "schedule_ingress_work", lambda work, **kwargs: jobs.append(work))
    return SimpleNamespace(start=start, observe=observe, jobs=jobs)


def test_verifies_raw_bytes_before_parse(ingress: SimpleNamespace) -> None:
    calls = []

    async def verify(ctx: object) -> bool:
        calls.append((ctx.request.headers["x-event"], ctx.raw_body))
        return False

    def parse(ctx: object) -> dict[str, object]:
        pytest.fail("unverified request reached parse")

    channel = make_channel(verify=verify, parse=parse)
    response = asyncio.run(channel.events(context(channel, b"\xff\x00")))
    assert response.status_code == 401
    assert calls == [("delivery-1", b"\xff\x00")]
    ingress.start.assert_not_called()


def test_accepts_without_post(ingress: SimpleNamespace) -> None:
    channel = make_channel()
    collect_channels([("channels/imessage.py", SimpleNamespace(channel=channel))])
    register_channel_messaging([channel])
    for user_id in ("alice", "bob"):
        channel.parse = lambda ctx, user_id=user_id: {
            "type": "message",
            "message": {**message(), "user_id": user_id},
        }
        response = asyncio.run(channel.events(context(channel)))
        assert response.status_code == 202
        assert json.loads(response.body)["status"] == "accepted"
        args = ingress.start.call_args.kwargs
        assert args["user_id"] == user_id
        assert args["thread_id"] == message()["thread_id"]
        assert "raw_event" not in args["binding"]
        runtime = resolve_runtime_channel(
            {"mda_channel": args["binding"]},
            {
                "langgraph_auth_user": {
                    "mda_source_provider": "orders",
                    "mda_source_thread_id": args["source_thread_key"],
                }
            },
        )
        assert runtime is not None
        assert runtime.raw_event is None
        assert args["binding"]["target"] == message()["target"]
    first, second = [call.kwargs for call in ingress.start.call_args_list]
    assert first["source_thread_key"] == second["source_thread_key"]
    assert requirements_from_channels([channel])["imessage"]["autoReply"] is False
    assert requirements_from_channels([channel])["imessage"]["eventsPath"] == (
        "/channels/imessage/events"
    )
    assert channel.messaging is None
    assert len(ingress.jobs) == 2
    for job in ingress.jobs:
        asyncio.run(job())


def test_receive_only_http_channel_does_not_get_slack_transport(
    ingress: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-bot-token")
    declarations = collect_channels(
        [
            ("channels/slack.py", SimpleNamespace(channel=channels.slack())),
            (
                "channels/receive_only_http.py",
                SimpleNamespace(channel=make_channel(provider="slack")),
            ),
        ]
    )
    app = build_managed_http_app(channels=declarations)
    status, _, _ = _call(app, "POST", "/channels/receive_only_http/events", body=b"{}")
    assert status == 202
    binding = ingress.start.call_args.kwargs["binding"]
    assert binding["name"] == "receive_only_http"
    assert binding["provider"] == "slack"
    runtime = resolve_runtime_channel(
        {"mda_channel": binding},
        {
            "langgraph_auth_user": {
                "mda_source_provider": "slack",
                "mda_source_thread_id": binding["source_thread_key"],
            }
        },
    )
    assert runtime is not None
    assert runtime.name == "receive_only_http"
    assert runtime.post is None
    assert len(ingress.jobs) == 1
    asyncio.run(ingress.jobs[0]())


def test_waits_for_acceptance(ingress: SimpleNamespace) -> None:
    async def run() -> None:
        accepted = asyncio.Event()
        starting = asyncio.Event()

        async def start(**kwargs: object) -> dict[str, str]:
            starting.set()
            await accepted.wait()
            return {"thread_id": "thread-1", "run_id": "run-1"}

        ingress.start.side_effect = start
        channel = make_channel(
            parse=lambda ctx: {"type": "message", "message": message(), "response": Response("ok")},
        )
        pending = asyncio.create_task(channel.events(context(channel)))
        await asyncio.wait_for(starting.wait(), timeout=1)
        assert not pending.done()
        accepted.set()
        assert (await pending).body == b"ok"

    asyncio.run(run())


def test_acceptance_failure_overrides_custom_response(ingress: SimpleNamespace) -> None:
    ingress.start.side_effect = RuntimeError("unavailable")
    on_error = AsyncMock()
    channel = make_channel(
        on_error=on_error,
        parse=lambda ctx: {"type": "message", "message": message(), "response": Response("ok")},
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 500
    ingress.observe.assert_not_called()
    on_error.assert_not_called()


@pytest.mark.parametrize(
    "content",
    ["answer", [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]],
)
def test_optional_post_runs_after_completion(ingress: SimpleNamespace, content: object) -> None:
    post = AsyncMock(return_value={"id": "reply"})
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": {**message(), "content": content}},
        post=post,
    )
    assert channel.messaging({}) is not None
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    assert ingress.start.call_args.kwargs["input"] == content
    post.assert_not_called()
    ingress.observe.return_value = {"reply_content": content}
    assert len(ingress.jobs) == 1
    asyncio.run(ingress.jobs[0]())
    post.assert_awaited_once_with(
        {"type": "content", "content": content, "target": message()["target"]}
    )


def test_post_failure_is_reported_after_acceptance(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    post = AsyncMock(side_effect=RuntimeError("provider failure"))
    channel = make_channel(post=post)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    post.assert_not_called()
    asyncio.run(ingress.jobs[0]())
    post.assert_awaited_once()
    assert "channel delivery failed (RuntimeError)" in caplog.text


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"type": "wrong"},
        {"type": "message", "message": {}},
        {"type": "ignore", "response": "ok"},
    ],
)
def test_rejects_invalid_parse_results(ingress: SimpleNamespace, result: object) -> None:
    channel = make_channel(parse=lambda ctx: result)
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.start.assert_not_called()


@pytest.mark.parametrize("verify_reads_json", [False, True])
def test_callbacks_can_read_request_body(
    ingress: SimpleNamespace, verify_reads_json: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MDA_ASSISTANT_ID")
    event = {"challenge": "verified"}
    raw = json.dumps(event).encode()

    async def verify(ctx: HttpChannelRequest) -> bool:
        assert ctx.raw_body == raw
        if verify_reads_json:
            assert await ctx.request.json() == event
        return True

    async def parse(ctx: HttpChannelRequest) -> HttpChannelParseResult:
        assert ctx.raw_body == raw
        return {"type": "ignore", "response": JSONResponse(await ctx.request.json())}

    channel = make_channel(verify=verify, parse=parse)
    app = build_managed_http_app(channels=[channel])
    status, body, _ = _call(app, "POST", "/channels/orders/events", body=raw)
    assert status == 200
    assert json.loads(body) == event
    ingress.start.assert_not_called()


@pytest.mark.parametrize("raw", [b"not json", b"", b"\xff\x00"])
def test_adapter_can_reject_invalid_json(ingress: SimpleNamespace, raw: bytes) -> None:
    async def parse(ctx: HttpChannelRequest) -> HttpChannelParseResult:
        await ctx.request.json()
        return {"type": "message", "message": message()}

    callback = AsyncMock(side_effect=parse)
    channel = make_channel(parse=callback)
    assert asyncio.run(channel.events(context(channel, raw))).status_code == 400
    callback.assert_awaited_once()
    ingress.start.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "content_type", "content"),
    [
        (
            b"text=hello+world&symbol=%E2%9C%93\r\n",
            "application/x-www-form-urlencoded",
            "hello world",
        ),
        (b"\xff\x00\x80\r\n", "application/octet-stream", "255,0,128,13,10"),
    ],
)
def test_adapter_decodes_signed_non_json(
    ingress: SimpleNamespace, raw: bytes, content_type: str, content: str
) -> None:
    def sign(body: bytes) -> str:
        return hmac.digest(b"test-signing-key", body, "sha256").hex()

    calls = []

    async def verify(ctx: HttpChannelRequest) -> bool:
        calls.append("verify")
        assert ctx.request.headers["content-type"] == content_type
        assert ctx.raw_body == raw
        body = await ctx.request.body()
        assert body == raw
        return hmac.compare_digest(sign(body), ctx.request.headers["x-signature"])

    async def parse(ctx: HttpChannelRequest) -> HttpChannelParseResult:
        calls.append("parse")
        assert ctx.raw_body == raw
        body = await ctx.request.body()
        content = (
            parse_qs(body.decode())["text"][0]
            if content_type == "application/x-www-form-urlencoded"
            else ",".join(str(byte) for byte in body)
        )
        return {
            "type": "message",
            "message": {**message(), "content": content},
            "raw_event": {"content": content},
        }

    channel = make_channel(verify=verify, parse=parse)
    app = build_managed_http_app(channels=[channel])
    status, _, _ = _call(
        app,
        "POST",
        "/channels/orders/events",
        body=raw,
        headers={
            "content-type": content_type,
            "x-signature": sign(raw),
        },
    )
    assert status == 202
    assert calls == ["verify", "parse"]
    args = ingress.start.call_args.kwargs
    assert args["input"] == content
    assert args["binding"]["raw_event"] == {"content": content}


@pytest.mark.parametrize("thread_id", ["", "order-1", " 11111111-1111-4111-8111-111111111111"])
def test_rejects_invalid_thread_uuid(ingress: SimpleNamespace, thread_id: str) -> None:
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": {**message(), "thread_id": thread_id}},
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.start.assert_not_called()


@pytest.mark.parametrize(
    "event", [None, False, 0, "", "event", ["one", "two"], {"order": {"count": 2}}]
)
def test_runtime_channel_preserves_raw_event_and_uses_context_target(
    ingress: SimpleNamespace, event: object
) -> None:
    async def run() -> None:
        post = AsyncMock(return_value={"id": "reply-1"})
        channel = make_channel(
            parse=lambda ctx: {"type": "message", "message": message(), "raw_event": event},
            post=post,
        )
        collect_channels([("channels/imessage.py", SimpleNamespace(channel=channel))])
        register_channel_messaging([channel])

        async def start(**kwargs: object) -> dict[str, str]:
            runtime = resolve_runtime_channel(
                {"mda_channel": kwargs["binding"]},
                {
                    "langgraph_auth_user": {
                        "mda_source_provider": "orders",
                        "mda_source_thread_id": kwargs["source_thread_key"],
                    }
                },
            )
            assert runtime is not None
            assert runtime.raw_event == (tuple(event) if isinstance(event, list) else event)
            assert runtime.event["type"] == "message"
            assert runtime.event["messages"][0]["content"] == "hello"
            assert not hasattr(runtime, "target")
            assert runtime.post is not None
            await runtime.post({"type": "content", "content": "posted during run"})
            await runtime.post({"type": "native", "native": {"blocks": [{"type": "section"}]}})
            for invalid in (
                {},
                {"type": "content", "content": "x", "native": {}},
                {"type": "content", "content": "x", "target": "other"},
                {"content": "missing type"},
                {"type": "unknown", "content": "wrong type"},
                {"type": "native", "content": "wrong payload"},
            ):
                with pytest.raises(ValueError, match=r"type|content|native|target"):
                    await runtime.post(invalid)
            return {"thread_id": "thread-1", "run_id": "run-1"}

        ingress.start.side_effect = start
        assert (await channel.events(context(channel))).status_code == 202
        args = ingress.start.call_args.kwargs
        assert args["binding"]["raw_event"] == event
        assert len(ingress.jobs) == 1
        await ingress.jobs[0]()
        assert post.await_count == 3
        assert post.await_args_list[0].args[0] == {
            "type": "content",
            "content": "posted during run",
            "target": message()["target"],
        }
        assert post.await_args_list[1].args[0] == {
            "type": "native",
            "native": {"blocks": [{"type": "section"}]},
            "target": message()["target"],
        }
        assert post.await_args_list[2].args[0] == {
            "type": "content",
            "content": "Hello from the agent",
            "target": message()["target"],
        }

    asyncio.run(run())


@pytest.mark.parametrize("target", [object(), float("nan"), {"bad": object()}])
def test_http_rejects_invalid_target(ingress: SimpleNamespace, target: object) -> None:
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": {**message(), "target": target}}
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.start.assert_not_called()


def test_http_requires_target(ingress: SimpleNamespace) -> None:
    inbound = message()
    inbound.pop("target")
    channel = make_channel(parse=lambda ctx: {"type": "message", "message": inbound})
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.start.assert_not_called()


def test_http_copies_message_and_raw_event_before_acceptance(ingress: SimpleNamespace) -> None:
    async def run() -> None:
        inbound = {**message(), "content": [{"type": "text", "text": "hello"}]}
        event = {"nested": {"text": "original"}}
        post = AsyncMock(return_value={"id": "reply"})
        channel = make_channel(
            parse=lambda ctx: {"type": "message", "message": inbound, "raw_event": event},
            post=post,
        )

        async def start(**kwargs: object) -> dict[str, str]:
            event["nested"]["text"] = "changed"
            assert kwargs["binding"]["raw_event"] == {"nested": {"text": "original"}}
            inbound["target"]["conversation_id"] = "changed"
            inbound["content"][0]["text"] = "changed"
            assert kwargs["input"] == [{"type": "text", "text": "hello"}]
            return {"thread_id": "thread-1", "run_id": "run-1"}

        ingress.start.side_effect = start
        assert (await channel.events(context(channel))).status_code == 202
        await ingress.jobs[0]()
        assert post.await_args.args[0]["target"] == message()["target"]

    asyncio.run(run())


def test_http_rejects_non_json_content_blocks(ingress: SimpleNamespace) -> None:
    channel = make_channel(
        parse=lambda ctx: {
            "type": "message",
            "message": {
                **message(),
                "content": [{"type": "text", "text": "hello", "extra": object()}],
            },
        }
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.start.assert_not_called()


@pytest.mark.parametrize(
    ("provider", "native"),
    [
        ("slack", {"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*Ready*"}}]}),
        ("photon", {"text": "Ready", "attachments": [{"url": "https://example.com/image.png"}]}),
    ],
)
def test_provider_native_replies_keep_the_bound_target(
    ingress: SimpleNamespace,
    provider: str,
    native: object,
) -> None:
    async def run() -> None:
        sent = AsyncMock(return_value={"id": "provider-message"})
        channel = make_channel(provider=provider, post=sent)
        collect_channels([("channels/native_reply.py", SimpleNamespace(channel=channel))])
        register_channel_messaging([channel])
        ctx = {**context(channel), "name": "native_reply"}
        assert (await channel.events(ctx)).status_code == 202
        binding = ingress.start.call_args.kwargs["binding"]
        runtime = resolve_runtime_channel(
            {"mda_channel": binding},
            {
                "langgraph_auth_user": {
                    "mda_source_provider": provider,
                    "mda_source_thread_id": binding["source_thread_key"],
                }
            },
        )
        assert runtime is not None
        assert runtime.post is not None
        await runtime.post({"type": "native", "native": native})
        sent.assert_awaited_once_with(
            {"type": "native", "native": native, "target": message()["target"]}
        )

    asyncio.run(run())


def test_http_accepts_null_target(ingress: SimpleNamespace) -> None:
    channel = make_channel(
        parse=lambda ctx: {
            "type": "message",
            "message": {
                **message(),
                "target": None,
            },
        }
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    assert ingress.start.call_args.kwargs["binding"]["target"] is None


@pytest.mark.parametrize("failure", ["observation", "post"])
def test_reports_failure_after_acceptance(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    error = RuntimeError("private provider details")
    post = AsyncMock(return_value={"id": "reply"})
    if failure == "post":
        post.side_effect = error
    else:
        ingress.observe.side_effect = error
    on_error = AsyncMock()
    channel = make_channel(post=post, on_error=on_error)
    response = asyncio.run(channel.events(context(channel)))
    assert response.status_code == 202
    assert b"private provider details" not in response.body
    on_error.assert_not_called()
    asyncio.run(ingress.jobs[0]())
    on_error.assert_awaited_once_with(error, message()["target"])
    assert "channel delivery failed" in caplog.text
    assert "private provider details" not in caplog.text
    if failure != "post":
        post.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_observes_callback_only_adapter(ingress: SimpleNamespace, fails: bool) -> None:
    error = RuntimeError("observation failed")
    if fails:
        ingress.observe.side_effect = error
    on_error = AsyncMock()
    channel = make_channel(on_error=on_error)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    assert channel.messaging is None
    assert channel.requirements("imessage")["autoReply"] is False
    assert len(ingress.jobs) == 1
    asyncio.run(ingress.jobs[0]())
    ingress.observe.assert_awaited_once()
    if fails:
        on_error.assert_awaited_once_with(error, message()["target"])
    else:
        on_error.assert_not_called()


def test_callback_failure_is_logged_without_retry(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    ingress.observe.side_effect = RuntimeError("private run details")
    on_error = AsyncMock(side_effect=ValueError("private callback details"))
    post = AsyncMock()
    channel = make_channel(post=post, on_error=on_error)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    asyncio.run(ingress.jobs[0]())
    on_error.assert_awaited_once()
    post.assert_not_called()
    assert caplog.messages == [
        "channel delivery failed (RuntimeError)",
        "channel error callback failed (ValueError)",
    ]


def test_suppressed_reply_does_not_call_error_callback(ingress: SimpleNamespace) -> None:
    ingress.observe.return_value = {"reply_content": None}
    post = AsyncMock()
    on_error = AsyncMock()
    channel = make_channel(post=post, on_error=on_error)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    asyncio.run(ingress.jobs[0]())
    post.assert_not_called()
    on_error.assert_not_called()


@pytest.mark.parametrize("delivery", ["sent", "failed", "no post"])
def test_default_error_reply(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture, delivery: str
) -> None:
    ingress.observe.side_effect = RuntimeError("private run details")
    post = AsyncMock(return_value={"id": "reply"})
    if delivery == "failed":
        post.side_effect = ValueError("private provider details")
    channel = make_channel(post=None if delivery == "no post" else post)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    post.assert_not_called()
    asyncio.run(ingress.jobs[0]())
    if delivery == "no post":
        post.assert_not_called()
    else:
        post.assert_awaited_once_with(
            {
                "type": "content",
                "content": "Something went wrong while processing your request.",
                "target": message()["target"],
            }
        )
    expected = ["channel delivery failed (RuntimeError)"]
    if delivery == "failed":
        expected.append("channel error reply failed (ValueError)")
    assert caplog.messages == expected


@pytest.mark.parametrize(
    "event", [float("nan"), float("inf"), {"bad": object()}, object(), {1: "bad"}]
)
def test_rejects_non_json_raw_event(ingress: SimpleNamespace, event: object) -> None:
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": message(), "raw_event": event}
    )
    response = asyncio.run(channel.events(context(channel)))
    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "invalid channel parse result"}
    ingress.start.assert_not_called()


def test_rejects_cyclic_raw_event(ingress: SimpleNamespace) -> None:
    event = []
    event.append(event)
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": message(), "raw_event": event}
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.start.assert_not_called()
