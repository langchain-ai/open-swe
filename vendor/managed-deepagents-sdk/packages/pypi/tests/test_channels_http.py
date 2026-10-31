from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import httpx
import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from managed_deepagents import (
    GitHubEvent,
    HttpChannelParseResult,
    HttpChannelRequest,
    _ingress,
    channels,
)
from managed_deepagents._channel import collect_channels, requirements_from_channels
from managed_deepagents._channels.binding import resolve_runtime_channel
from managed_deepagents._channels.github import reset_github_app_values
from managed_deepagents._channels.github_events import github_event
from managed_deepagents._channels.github_schema import SUPPORTED_EVENT_KEYS
from managed_deepagents._channels.github_types import GitHubChannelThread
from managed_deepagents._channels.http import ChannelName, channel_thread_id
from managed_deepagents._channels.http import http as http_module
from managed_deepagents._channels.http import ingress as http_ingress
from managed_deepagents._channels.http.studio import studio_url
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._channels.run_input import InvalidRunInput
from managed_deepagents._identity_http import build_managed_http_app
from tests.test_identity_http import _call

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from managed_deepagents._channels.http import HttpChannel
    from managed_deepagents._channels.http.types import HttpDelivery


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
    observe = AsyncMock(return_value={"status": "success", "interrupts": []})
    wait = AsyncMock()
    cancel = AsyncMock(return_value=[])
    waits_on = AsyncMock(return_value={"interrupts": []})
    monkeypatch.setattr(http_ingress, "http_ingress_thread_waits_on", waits_on)
    monkeypatch.setattr(http_ingress, "start_http_ingress_agent", start)
    monkeypatch.setattr(http_ingress, "cancel_http_ingress_runs", cancel)
    monkeypatch.setattr(http_ingress, "observe_http_ingress_agent", observe)
    monkeypatch.setattr(http_ingress, "wait_for_http_ingress_thread", wait)
    # Each test starts with no messages waiting for their threads.
    monkeypatch.setattr(http_module, "_thread_waiters", {})
    jobs = []
    monkeypatch.setattr(_ingress, "schedule_ingress_work", lambda work, **kwargs: jobs.append(work))
    # What each started run was prepared with.
    prepared: list[object] = []
    return SimpleNamespace(
        start=start,
        observe=observe,
        wait=wait,
        cancel=cancel,
        waits_on=waits_on,
        jobs=jobs,
        prepared=prepared,
    )


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
def test_leaves_a_completed_runs_answer_to_the_run(
    ingress: SimpleNamespace, content: object
) -> None:
    post = AsyncMock(return_value={"id": "reply"})
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": {**message(), "content": content}},
        post=post,
    )
    assert channel.messaging({}) is not None
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    assert ingress.start.call_args.kwargs["input"] == content
    post.assert_not_called()
    ingress.observe.return_value = {"status": "success", "interrupts": []}
    assert len(ingress.jobs) == 1
    asyncio.run(ingress.jobs[0]())
    # The run posts it (reply.py), so a restart here doesn't lose it.
    post.assert_not_called()


def test_a_failing_error_reply_is_logged_after_acceptance(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    ingress.observe.return_value = {"status": "error", "interrupts": []}
    post = AsyncMock(side_effect=RuntimeError("provider failure"))
    channel = make_channel(post=post)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    post.assert_not_called()
    asyncio.run(ingress.jobs[0]())
    post.assert_awaited_once()
    assert "channel run failed" in caplog.text
    assert "channel error reply failed (RuntimeError)" in caplog.text


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


THREAD = "11111111-1111-4111-8111-111111111111"
TARGET = {"conversation_id": "provider-conversation"}


def _drain(ingress: SimpleNamespace) -> None:
    async def drain() -> None:
        while ingress.jobs:
            await ingress.jobs.pop(0)()

    asyncio.run(drain())


@pytest.mark.parametrize(
    "outcome",
    [
        {"thread_id": "thread-1", "run_id": "run-1"},
        {"skipped": "busy"},
        {"skipped": "paused"},
        {"skipped": "duplicate"},
    ],
)
def test_tells_the_channel_about_each_received_message(
    ingress: SimpleNamespace, outcome: dict[str, str]
) -> None:
    ingress.start.return_value = outcome
    on_received = AsyncMock()
    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": message(), "raw_event": {"order": "one"}},
        on_received=on_received,
    )
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    # The notice is scheduled before MDA decides what the message does.
    asyncio.run(ingress.jobs[0]())
    on_received.assert_awaited_once_with(
        TARGET, {"thread_id": THREAD, "assistant_id": "agent", "raw_event": {"order": "one"}}
    )


def test_ignored_messages_and_failed_notices_never_stop_a_run(ingress: SimpleNamespace) -> None:
    on_received = AsyncMock(side_effect=RuntimeError("provider down"))
    ignored = make_channel(parse=lambda ctx: {"type": "ignore"}, on_received=on_received)
    asyncio.run(ignored.events(context(ignored)))
    assert ingress.jobs == []

    channel = make_channel(on_received=on_received)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    ingress.start.assert_awaited_once()
    asyncio.run(ingress.jobs[0]())


_PENDING = [{"id": "i-1", "value": {"question": "Ship it?"}}]


@pytest.mark.parametrize(
    ("observed", "assistant_id"),
    [
        ({"status": "success", "interrupts": []}, "agent"),
        ({"status": "interrupted", "interrupts": _PENDING, "assistant_id": "agent-7"}, "agent-7"),
        ({"status": "interrupted", "interrupts": []}, "agent"),
        ({"status": "error", "interrupts": []}, "agent"),
        ({"status": "timeout", "interrupts": []}, "agent"),
    ],
)
def test_finishes_a_run_by_how_it_ended(
    ingress: SimpleNamespace, observed: dict[str, Any], assistant_id: str
) -> None:
    calls: list[str] = []
    on_finish = AsyncMock(side_effect=lambda *_: calls.append("finish"))
    on_error = AsyncMock(side_effect=lambda *_: calls.append("error"))
    post = AsyncMock(return_value={"id": "posted"})
    ingress.observe.return_value = observed
    channel = make_channel(
        on_finish=on_finish,
        on_error=on_error,
        post=post,
        parse=lambda ctx: {"type": "message", "message": message(), "raw_event": {"order": 1}},
    )
    asyncio.run(channel.events(context(channel)))
    _drain(ingress)
    on_finish.assert_awaited_once_with(
        TARGET,
        {
            "thread_id": THREAD,
            "assistant_id": assistant_id,
            "raw_event": {"order": 1},
            "type": "run",
            "run": {"id": "run-1", "status": observed["status"]},
            "interrupts": observed["interrupts"],
        },
    )
    # Only a failure is reported, by ``on_error`` first; the run posts the rest.
    failed = observed["status"] in ("error", "timeout")
    assert calls == (["error", "finish"] if failed else ["finish"])
    post.assert_not_called()


@pytest.mark.parametrize(
    "observed", [RuntimeError("join failed"), None], ids=["unobserved", "not-ended"]
)
def test_finishes_a_run_it_lost_track_of_as_failed_without_an_error(
    ingress: SimpleNamespace, observed: object
) -> None:
    if isinstance(observed, Exception):
        ingress.observe.side_effect = observed
    else:
        ingress.observe.return_value = observed
    on_finish = AsyncMock()
    on_error = AsyncMock()
    channel = make_channel(on_finish=on_finish, on_error=on_error)
    asyncio.run(channel.events(context(channel)))
    _drain(ingress)
    # It may still answer.
    on_error.assert_not_called()
    on_finish.assert_awaited_once_with(
        TARGET,
        {
            "thread_id": THREAD,
            "assistant_id": "agent",
            "type": "failed",
            "reason": "observe",
            "interrupts": [],
        },
    )


@pytest.mark.parametrize(
    ("skipped", "reason", "interrupts", "assistant_id"),
    [
        (
            {"skipped": "paused", "assistant_id": "a-1", "interrupts": _PENDING},
            "waiting",
            _PENDING,
            "a-1",
        ),
        ({"skipped": "duplicate"}, "duplicate", [], "agent"),
        ({"skipped": "rejected"}, "rejected", [], "agent"),
        ({"skipped": "ignored"}, "ignored", [], "agent"),
    ],
)
def test_finishes_a_message_the_thread_took_no_run_for_saying_why(
    ingress: SimpleNamespace,
    skipped: dict[str, Any],
    reason: str,
    interrupts: list[dict[str, Any]],
    assistant_id: str,
) -> None:
    on_finish = AsyncMock()
    ingress.start.side_effect = [skipped]
    channel = make_channel(on_finish=on_finish)
    response = asyncio.run(channel.events(context(channel)))
    assert json.loads(bytes(response.body)) == {"status": skipped["skipped"]}
    _drain(ingress)
    on_finish.assert_awaited_once_with(
        TARGET,
        {
            "thread_id": THREAD,
            "assistant_id": assistant_id,
            "type": "skipped",
            "reason": reason,
            "interrupts": interrupts,
        },
    )


def test_finishes_a_message_once_its_busy_thread_turns_out_to_be_waiting(
    ingress: SimpleNamespace,
) -> None:
    on_finish = AsyncMock()
    ingress.start.side_effect = [{"skipped": "busy"}, {"skipped": "paused", "interrupts": _PENDING}]
    channel = make_channel(on_finish=on_finish)
    asyncio.run(channel.events(context(channel)))
    _drain(ingress)
    assert ingress.start.await_count == 2
    on_finish.assert_awaited_once_with(
        TARGET,
        {
            "thread_id": THREAD,
            "assistant_id": "agent",
            "type": "skipped",
            "reason": "waiting",
            "interrupts": _PENDING,
        },
    )


@pytest.mark.parametrize("background", [False, True])
def test_finishes_a_message_whose_run_could_not_be_started_as_failed(
    ingress: SimpleNamespace, background: bool
) -> None:
    on_finish = AsyncMock()
    ingress.start.side_effect = RuntimeError("server down")
    channel = make_channel(
        on_finish=on_finish,
        parse=lambda ctx: {"type": "message", "message": {**message(), "background": background}},
    )
    # Unless it already has its answer, the provider may redeliver it: that
    # failure isn't final.
    status = asyncio.run(channel.events(context(channel))).status_code
    assert status == (202 if background else 500)
    _drain(ingress)
    on_finish.assert_awaited_once_with(
        TARGET,
        {
            "thread_id": THREAD,
            "assistant_id": "agent",
            "type": "failed",
            "reason": "start" if background else "retry",
            "interrupts": [],
        },
    )


def test_finishes_a_message_only_after_it_was_received(ingress: SimpleNamespace) -> None:
    calls: list[str] = []
    ingress.start.side_effect = [{"skipped": "duplicate"}]
    channel = make_channel(
        on_received=AsyncMock(side_effect=lambda *_: calls.append("received")),
        on_finish=AsyncMock(side_effect=lambda *_: calls.append("finish")),
    )

    async def run() -> None:
        await channel.events(context(channel))
        receive, finish = ingress.jobs
        # Whichever job runs first, the finish notice waits for the received one.
        finishing = asyncio.ensure_future(finish())
        await asyncio.sleep(0)
        await receive()
        await finishing

    asyncio.run(run())
    assert calls == ["received", "finish"]


def test_logs_a_failing_finish_notice_without_its_text(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    channel = make_channel(on_finish=AsyncMock(side_effect=RuntimeError("secret-token")))
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    _drain(ingress)
    assert "channel finish notice failed (RuntimeError)" in caplog.text
    assert "secret-token" not in caplog.text


def test_cancels_the_threads_runs_then_finishes_it(ingress: SimpleNamespace) -> None:
    ingress.cancel.return_value = ["run-1", "run-2"]
    stop = {key: value for key, value in message().items() if key != "content"}
    on_finish = AsyncMock()
    on_received = AsyncMock()
    channel = make_channel(
        parse=lambda ctx: {"type": "cancel", "message": stop, "raw_event": {"stop": True}},
        on_finish=on_finish,
        on_received=on_received,
    )
    response = asyncio.run(channel.events(context(channel)))
    assert response.status_code == 202
    assert json.loads(bytes(response.body)) == {"status": "cancelled", "cancelled": 2}
    ingress.start.assert_not_called()
    ingress.cancel.assert_awaited_once_with(
        user_id="photon:line-1:user-1",
        source_thread_key='http:v1:["imessage","' + THREAD + '"]',
        source_provider="orders",
        thread_id=THREAD,
    )
    # A thread that waited on a decision still does.
    ingress.waits_on.return_value = {"interrupts": _PENDING, "assistant_id": "a-1"}
    _drain(ingress)
    # A cancel starts no run of its own.
    on_finish.assert_awaited_once_with(
        TARGET,
        {
            "thread_id": THREAD,
            "assistant_id": "a-1",
            "raw_event": {"stop": True},
            "type": "skipped",
            "reason": "cancel",
            "interrupts": _PENDING,
        },
    )
    # A cancel finishes at once; it isn't a message that waits for its thread.
    on_received.assert_not_called()


def test_finishes_both_the_stop_and_the_run_it_cancelled(ingress: SimpleNamespace) -> None:
    stop = {key: value for key, value in message().items() if key != "content"}
    on_finish = AsyncMock()
    ingress.cancel.return_value = ["run-1"]
    ingress.observe.return_value = {"status": "interrupted", "interrupts": []}
    cancel = {"value": False}

    def parse(ctx: object) -> dict[str, object]:
        if cancel["value"]:
            return {"type": "cancel", "message": stop}
        return {"type": "message", "message": message()}

    channel = make_channel(parse=parse, on_finish=on_finish)

    async def run() -> None:
        await channel.events(context(channel))
        cancel["value"] = True
        await channel.events(context(channel))
        while ingress.jobs:
            await ingress.jobs.pop(0)()

    asyncio.run(run())
    runs = [call.args[1].get("run") for call in on_finish.await_args_list]
    assert len(runs) == 2
    assert None in runs
    assert {"id": "run-1", "status": "interrupted"} in runs


def test_a_cancel_carrying_what_only_a_message_takes_is_refused(
    ingress: SimpleNamespace,
) -> None:
    # `content` starts nothing on a cancel, so it is a channel mistake.
    channel = make_channel(parse=lambda ctx: {"type": "cancel", "message": message()})
    assert asyncio.run(channel.events(context(channel))).status_code == 400
    ingress.cancel.assert_not_called()


def test_drops_a_message_that_waited_for_its_busy_thread_when_a_cancel_arrives(
    ingress: SimpleNamespace,
) -> None:
    stop = {key: value for key, value in message().items() if key != "content"}
    cancel = {"now": False}
    ingress.cancel.return_value = ["run-1"]
    ingress.start.side_effect = [{"skipped": "busy"}]
    on_finish = AsyncMock()
    channel = make_channel(
        parse=lambda ctx: (
            {"type": "cancel", "message": stop}
            if cancel["now"]
            else {"type": "message", "message": message()}
        ),
        on_finish=on_finish,
    )

    async def send() -> None:
        await channel.events(context(channel))

    async def drain() -> None:
        while ingress.jobs:
            await ingress.jobs.pop(0)()

    asyncio.run(send())
    cancel["now"] = True
    asyncio.run(send())
    asyncio.run(drain())
    # The waiting message never starts once the thread was cancelled.
    assert ingress.start.await_count == 1
    # The stop and the dropped message both finish without a run.
    finished = [call.args[1] for call in on_finish.await_args_list]
    assert len(finished) == 2
    assert all("run" not in delivery for delivery in finished)

    # A cancel that lands while the message's first start is in flight drops
    # it too, once the start reports the thread busy.
    cancel["now"] = False

    async def start_during_cancel(**_kwargs: object) -> dict[str, str]:
        cancel["now"] = True
        await send()
        cancel["now"] = False
        return {"skipped": "busy"}

    ingress.start.side_effect = start_during_cancel
    asyncio.run(send())
    asyncio.run(drain())
    assert ingress.start.await_count == 2
    # Nothing is kept for threads without waiting messages.
    assert http_module._thread_waiters == {}

    # A message arriving after the cancel waits and starts as usual.
    cancel["now"] = False
    ingress.start.side_effect = [{"skipped": "busy"}, {"thread_id": "thread-1", "run_id": "run-2"}]
    ingress.observe.return_value = {"status": "success", "interrupts": []}
    asyncio.run(send())
    asyncio.run(drain())
    assert ingress.start.await_count == 4
    assert http_module._thread_waiters == {}


def _background_message() -> dict[str, object]:
    without = {key: value for key, value in message().items() if key != "content"}
    return {**without, "background": True}


def _starts_prepared(ingress: SimpleNamespace) -> None:
    async def start(**kwargs: Any) -> dict[str, str]:
        ingress.prepared.append(await kwargs["prepare"]())
        return {"thread_id": "thread-1", "run_id": "run-1"}

    ingress.start.side_effect = start


def test_answers_first_for_a_background_message_then_starts_its_run(
    ingress: SimpleNamespace,
) -> None:
    _starts_prepared(ingress)

    async def run_input(_message: object, _raw: object) -> str:
        return "hello"

    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": _background_message()},
        input=run_input,
    )
    response = asyncio.run(channel.events(context(channel)))
    assert response.status_code == 202
    assert json.loads(response.body)["status"] == "accepted"
    ingress.start.assert_not_called()

    asyncio.run(ingress.jobs.pop(0)())
    [prepared] = ingress.prepared
    assert prepared["messages"][0]["content"] == "hello"
    # The run it started is observed, as a message's that waited for it.
    assert len(ingress.jobs) == 1


@pytest.mark.parametrize(
    ("result", "logged"),
    [
        (
            ("hello", {"metadata": {"ls_repo_provider": "github"}}),
            "channel input refused, no run started: run metadata key is reserved: ls_repo_provider",
        ),
        (
            ("hello", {"context": {"channel": {}}}),
            "channel input refused, no run started: context.channel is reserved",
        ),
    ],
)
def test_logs_why_it_refused_a_background_message_input(
    ingress: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
    result: object,
    logged: str,
) -> None:
    _starts_prepared(ingress)

    async def run_input(_message: object, _raw: object) -> object:
        return result

    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": _background_message()},
        input=run_input,
    )
    asyncio.run(channel.events(context(channel)))
    with caplog.at_level(logging.ERROR, logger="mda"):
        asyncio.run(ingress.jobs.pop(0)())
    assert ingress.prepared == [None]
    assert logged in caplog.text


def test_logs_only_the_type_of_an_input_that_raises(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    _starts_prepared(ingress)

    async def run_input(_message: object, _raw: object) -> object:
        # Even the reason MDA would log is the input's own message here.
        raise InvalidRunInput("secret detail")

    channel = make_channel(
        parse=lambda ctx: {"type": "message", "message": _background_message()},
        input=run_input,
    )
    asyncio.run(channel.events(context(channel)))
    with caplog.at_level(logging.ERROR, logger="mda"):
        asyncio.run(ingress.jobs.pop(0)())
    assert "channel input failed (InvalidRunInput)" in caplog.text
    assert "secret detail" not in caplog.text


def test_drops_a_background_message_cancelled_while_its_input_is_decided(
    ingress: SimpleNamespace,
) -> None:
    async def start(**kwargs: Any) -> dict[str, str]:
        prepared = await kwargs["prepare"]()
        ingress.prepared.append(prepared)
        return {"skipped": "ignored"} if prepared is None else {"thread_id": "t", "run_id": "r"}

    ingress.start.side_effect = start
    stop = {key: value for key, value in message().items() if key != "content"}
    cancel = {"now": False}
    on_finish = AsyncMock()

    async def run_input(_message: object, _raw: object) -> str:
        cancel["now"] = True
        assert (await channel.events(context(channel))).status_code == 202
        return "hello"

    channel = make_channel(
        parse=lambda ctx: (
            {"type": "cancel", "message": stop}
            if cancel["now"]
            else {"type": "message", "message": _background_message()}
        ),
        input=run_input,
        on_finish=on_finish,
    )
    asyncio.run(channel.events(context(channel)))
    asyncio.run(ingress.jobs.pop(0)())
    assert ingress.prepared == [None]
    _drain(ingress)
    reasons = sorted(call.args[1]["reason"] for call in on_finish.await_args_list)
    assert reasons == ["cancel", "dropped"]


def test_a_failed_cancel_is_reported_without_telling_the_channel(
    ingress: SimpleNamespace,
) -> None:
    ingress.cancel.side_effect = RuntimeError("server down")
    on_finish = AsyncMock()
    stop = {key: value for key, value in message().items() if key != "content"}
    channel = make_channel(
        parse=lambda ctx: {"type": "cancel", "message": stop}, on_finish=on_finish
    )
    response = asyncio.run(channel.events(context(channel)))
    assert response.status_code == 500
    assert json.loads(bytes(response.body)) == {"error": "channel run could not be cancelled"}
    assert ingress.jobs == []

    # A cancel still names a valid thread.
    invalid = make_channel(
        parse=lambda ctx: {"type": "cancel", "message": {**stop, "thread_id": "x"}}
    )
    assert asyncio.run(invalid.events(context(invalid))).status_code == 400


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
        # The run posts its own answer (reply.py), not the delivery.
        assert post.await_count == 2
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
        ingress.observe.return_value = {"status": "error", "interrupts": []}
        assert (await channel.events(context(channel))).status_code == 202
        await ingress.jobs[0]()
        # The error reply goes to the target as parsed, not as mutated after.
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


def test_reports_failure_after_acceptance(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    post = AsyncMock(return_value={"id": "reply"})
    ingress.observe.return_value = {"status": "error", "interrupts": []}
    on_error = AsyncMock()
    channel = make_channel(post=post, on_error=on_error)
    response = asyncio.run(channel.events(context(channel)))
    assert response.status_code == 202
    on_error.assert_not_called()
    asyncio.run(ingress.jobs[0]())
    on_error.assert_awaited_once()
    error, target = on_error.await_args.args
    assert (getattr(error, "code", None), target) == ("channel_run_failed", message()["target"])
    assert "channel run failed" in caplog.text
    post.assert_not_called()


def test_reports_no_error_for_a_run_it_cannot_observe(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    # The run may have answered already, so only its own failure is reported.
    ingress.observe.side_effect = RuntimeError("join failed")
    post = AsyncMock(return_value={"id": "reply"})
    on_error = AsyncMock()
    channel = make_channel(post=post, on_error=on_error)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    asyncio.run(ingress.jobs[0]())
    on_error.assert_not_called()
    post.assert_not_called()
    assert caplog.messages == ["[mda] channel run could not be observed (RuntimeError)"]


def test_declares_a_channel_that_leaves_replying_to_the_agent() -> None:
    post = AsyncMock(return_value={"id": "posted"})
    assert make_channel(post=post).requirements("imessage")["autoReply"] is True
    # A hand-written channel's setup provides no connections.
    assert "agentConnections" not in make_channel(post=post).requirements("imessage")
    channel = make_channel(post=post, reply="none")
    assert channel.requirements("imessage")["autoReply"] is False
    # The agent still posts through the channel's transport.
    assert channel.messaging is not None
    with pytest.raises(TypeError, match="channel reply must be auto or none"):
        make_channel(post=post, reply="sometimes")


@pytest.mark.parametrize("fails", [False, True])
def test_observes_callback_only_adapter(ingress: SimpleNamespace, fails: bool) -> None:
    if fails:
        ingress.observe.return_value = {"status": "error", "interrupts": []}
    on_error = AsyncMock()
    channel = make_channel(on_error=on_error)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    assert channel.messaging is None
    assert channel.requirements("imessage")["autoReply"] is False
    assert len(ingress.jobs) == 1
    asyncio.run(ingress.jobs[0]())
    ingress.observe.assert_awaited_once()
    if fails:
        on_error.assert_awaited_once()
    else:
        on_error.assert_not_called()


def test_callback_failure_is_logged_without_retry(
    ingress: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    ingress.observe.return_value = {"status": "error", "interrupts": []}
    on_error = AsyncMock(side_effect=ValueError("private callback details"))
    post = AsyncMock()
    channel = make_channel(post=post, on_error=on_error)
    assert asyncio.run(channel.events(context(channel))).status_code == 202
    asyncio.run(ingress.jobs[0]())
    on_error.assert_awaited_once()
    post.assert_not_called()
    assert caplog.messages == [
        '[mda] channel run failed {"threadId": "thread-1"}',
        "[mda] channel error callback failed (ValueError)",
    ]


def test_a_run_that_answered_does_not_call_error_callback(ingress: SimpleNamespace) -> None:
    ingress.observe.return_value = {"status": "success", "interrupts": []}
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
    ingress.observe.return_value = {"status": "error", "interrupts": []}
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
    expected = ['[mda] channel run failed {"threadId": "thread-1"}']
    if delivery == "failed":
        expected.append("[mda] channel error reply failed (ValueError)")
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


def test_github_receipt(
    ingress: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="mda")
    monkeypatch.delenv("GH_WEBHOOK_SECRET", raising=False)
    channel = channels.github()
    monkeypatch.setenv("GH_WEBHOOK_SECRET", "receipt-key")
    body = b'{ "action": "opened", "body": "private text", "issue": {"number": 42} }'

    def send(event: str, signature: str | None = None, raw: bytes = body) -> int:
        signed = (
            signature
            if signature is not None
            else "sha256=" + hmac.new(b"receipt-key", raw, "sha256").hexdigest()
        )
        ctx = context(channel, raw)
        ctx["request"] = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/channels/github/events",
                "headers": [
                    (b"x-github-event", event.encode()),
                    (b"x-github-delivery", b"delivery-1"),
                    (b"x-hub-signature-256", signed.encode()),
                ],
            }
        )
        return asyncio.run(channel.events(ctx)).status_code

    for event, status in (("ping", 200), ("issues", 202), ("push", 202)):
        assert send(event) == status
    for signature in ("", "sha256=bad", "sha256=" + "0" * 64):
        assert send("issues", signature) == 401
    signature = "sha256=" + hmac.new(b"receipt-key", body, "sha256").hexdigest()
    assert send("issues", signature, body + b" ") == 401
    assert send("issues", raw=b"not json") == 400
    monkeypatch.delenv("GH_WEBHOOK_SECRET")
    assert send("issues") == 401
    assert channel.messaging is None
    ingress.start.assert_not_called()
    assert not ingress.jobs
    assert "private text" not in caplog.text
    assert '"issue": 42' in caplog.text


@pytest.mark.parametrize(
    ("payload", "event", "delivery", "status"),
    [
        (None, "issues", "delivery-1", 400),
        ([], "issues", "delivery-1", 400),
        (42, "issues", "delivery-1", 400),
        ({"action": None}, "issues", "delivery-1", 400),
        ({"action": 42}, "issues", "delivery-1", 400),
        ({"repository": None}, "issues", "delivery-1", 400),
        ({"issue": []}, "issues", "delivery-1", 400),
        ({"issue": {"number": "42"}}, "issues", "delivery-1", 400),
        ({"pull_request": {"number": True}}, "issues", "delivery-1", 400),
        ({"repository": {"id": 1.5}}, "issues", "delivery-1", 400),
        ({"repository": {"id": 9007199254740992}}, "issues", "delivery-1", 400),
        ({}, None, "delivery-1", 400),
        ({}, " ", "delivery-1", 400),
        ({}, "issues", None, 400),
        ({}, "issues", " ", 400),
        ({"zen": "hello"}, "ping", "delivery-1", 200),
        ({"future": {"arbitrary": True}}, "future_event", "delivery-1", 202),
        (
            {"repository": {}, "issue": {"number": 42, "body": "private"}},
            "issues",
            "delivery-1",
            202,
        ),
    ],
)
def test_github_receipt_validation(
    ingress: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    event: str | None,
    delivery: str | None,
    status: int,
) -> None:
    monkeypatch.setenv("GH_WEBHOOK_SECRET", "receipt-key")
    channel = channels.github()
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"receipt-key", raw, "sha256").hexdigest()
    headers = [(b"x-hub-signature-256", signature.encode())]
    if event is not None:
        headers.append((b"x-github-event", event.encode()))
    if delivery is not None:
        headers.append((b"x-github-delivery", delivery.encode()))
    ctx = context(channel, raw)
    ctx["request"] = Request(
        {"type": "http", "method": "POST", "path": "/channels/github/events", "headers": headers}
    )
    response = asyncio.run(channel.events(ctx))
    assert response.status_code == status
    if status == 400:
        assert json.loads(response.body) == {"error": "invalid channel payload"}
    ingress.start.assert_not_called()
    assert not ingress.jobs
    headers[0] = (b"x-hub-signature-256", b"sha256=bad")
    ctx["request"] = Request(
        {"type": "http", "method": "POST", "path": "/channels/github/events", "headers": headers}
    )
    # A ping starts nothing, so it is answered without a signature.
    assert asyncio.run(channel.events(ctx)).status_code == (200 if event == "ping" else 401)


GITHUB_KEYS = (
    "issues.opened",
    "issues.edited",
    "issues.reopened",
    "issues.labeled",
    "issue_comment.created",
    "issue_comment.edited",
    "pull_request.opened",
    "pull_request.edited",
    "pull_request.reopened",
    "pull_request.ready_for_review",
    "pull_request.converted_to_draft",
    "pull_request.synchronize",
    "pull_request.labeled",
    "pull_request.closed",
    "pull_request_review.submitted",
    "pull_request_review.edited",
    "pull_request_review.dismissed",
    "pull_request_review_comment.created",
    "pull_request_review_comment.edited",
    "push",
    "status",
    "check_run.completed",
    "check_suite.completed",
    "workflow_run.completed",
)


def github_payload(key: str = "issues.opened") -> dict[str, object]:
    name, _, action = key.partition(".")
    issue = {"number": 1, "title": "untrusted title", "body": "private payload"}
    pull_request = {"number": 2, "title": "PR", "body": None, "draft": False, "merged": True}
    comment = {"id": 4, "body": "comment"}
    subjects = {
        "issues": {"issue": issue},
        "issue_comment": {"issue": issue, "comment": comment},
        "pull_request": {"pull_request": pull_request},
        "pull_request_review": {
            "pull_request": pull_request,
            "review": {"id": 5, "body": None, "state": "approved"},
        },
        "pull_request_review_comment": {"pull_request": pull_request, "comment": comment},
        "workflow_run": {"workflow_run": {"id": 6, "conclusion": "failure", "pull_requests": []}},
        "check_run": {
            "check_run": {
                "id": 8,
                "name": "test",
                "head_sha": "abc123",
                "conclusion": "failure",
                "pull_requests": [],
            }
        },
        "check_suite": {
            "check_suite": {
                "id": 9,
                "head_sha": "abc123",
                "conclusion": "success",
                "pull_requests": [],
            }
        },
        "push": {"ref": "refs/heads/main", "before": "abc122", "after": "abc123"},
        "status": {"id": 10, "sha": "abc123", "state": "failure", "context": "ci/test"},
    }
    return {
        # Events without an action, such as `push`, carry none.
        **({"action": action} if action else {}),
        "installation": {"id": 12},
        "repository": {
            "id": 3,
            "owner": {"login": "owner"},
            "name": "repo",
            "full_name": "owner/repo",
            "private": True,
        },
        "sender": {"id": 7, "login": "person", "type": "User"},
        **subjects.get(name, {}),
        **({"label": {"name": "triage"}} if action == "labeled" else {}),
    }


async def github_send(
    channel: HttpChannel,
    key: str = "issues.opened",
    data: object = None,
    delivery: str = "delivery-1",
    signature: str | None = None,
) -> Response:
    if channel.name is None:
        collect_channels([("channels/mygithub.py", SimpleNamespace(channel=channel))])
    raw = json.dumps(github_payload(key) if data is None else data).encode()
    ctx = context(channel, raw)
    ctx["name"] = "mygithub"
    ctx["request"] = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/channels/mygithub/events",
            "headers": [
                (b"x-github-event", key.split(".", maxsplit=1)[0].encode()),
                (b"x-github-delivery", delivery.encode()),
                (
                    b"x-hub-signature-256",
                    (
                        signature
                        if signature is not None
                        else "sha256=" + hmac.new(b"test-secret", raw, "sha256").hexdigest()
                    ).encode(),
                ),
            ],
        }
    )
    return await channel.events(ctx)


def ready_thread(ingress: SimpleNamespace) -> Callable[..., Awaitable[dict[str, object]]]:
    """An idle thread: the run starts with what the handler returns."""

    async def start(**kwargs: Any) -> dict[str, object]:
        prepare = kwargs.get("prepare")
        prepared = None if prepare is None else await prepare()
        ingress.prepared.append(prepared)
        if prepared is None:
            return {"skipped": "ignored"}
        return {"thread_id": kwargs["thread_id"], "run_id": "run-1"}

    return start


@pytest.fixture
def github_env(monkeypatch: pytest.MonkeyPatch, ingress: SimpleNamespace) -> Iterator[None]:
    monkeypatch.setenv("GH_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GH_APP_BOT_ID", "99")
    ingress.start.side_effect = ready_thread(ingress)
    reset_github_app_values()
    yield
    reset_github_app_values()


async def drain_github(ingress: SimpleNamespace) -> None:
    while ingress.jobs:
        await ingress.jobs.pop(0)()


@pytest.mark.parametrize("key", GITHUB_KEYS)
def test_github_dispatches_supported_key(
    key: str, ingress: SimpleNamespace, github_env: None
) -> None:
    seen = []

    def handler(event: object, thread: object) -> str:
        seen.append((event, thread))
        return "  prompt  "

    channel = channels.github(on_event={key: handler})

    async def run() -> None:
        response = await github_send(channel, key)
        assert response.status_code == 202
        assert json.loads(response.body) == {"status": "received"}
        assert len(seen) == 1
        ingress.start.assert_called_once()
        await drain_github(ingress)

    asyncio.run(run())
    event, thread = seen[0]
    assert event.key == key
    assert event.delivery_id == "delivery-1"
    assert event.repository.full_name == "owner/repo"
    assert event.payload == github_payload(key)
    assert thread.channel.name == "mygithub"
    # The thread the run starts on, so run context can name it.
    assert thread.id == ingress.start.call_args.kwargs["thread_id"]
    ingress.start.assert_called_once()
    args = ingress.start.call_args.kwargs
    assert args["user_id"] == "github:7"
    assert args["source_provider"] == "github"
    # The handler's string is the run's user message.
    assert ingress.prepared == [
        {
            "messages": [
                {
                    "role": "human",
                    "content": "  prompt  ",
                    "additional_kwargs": {},
                    "response_metadata": {},
                }
            ],
            "state": {},
        }
    ]
    assert args["source_thread_key"] == 'http:v1:["mygithub","' + args["thread_id"] + '"]'
    assert args["binding"]["raw_event"]["delivery_id"] == "delivery-1"
    ingress.observe.assert_called_once()


def test_github_keeps_one_thread_per_issue_or_pull_request(
    ingress: SimpleNamespace, github_env: None
) -> None:
    channel = channels.github(
        on_event={
            "issues.opened": lambda event, runtime: "prompt",
            "issue_comment.created": lambda event, runtime: "prompt",
            "pull_request.opened": lambda event, runtime: "prompt",
            "pull_request_review.submitted": lambda event, runtime: "prompt",
        }
    )
    for index, key in enumerate(
        (
            "issues.opened",
            "issue_comment.created",
            "pull_request.opened",
            "pull_request_review.submitted",
        )
    ):
        asyncio.run(github_send(channel, key, delivery=f"d-{index + 1}"))
    calls = [call.kwargs for call in ingress.start.call_args_list]
    issue = channel_thread_id("mygithub", "3/1")
    pull = channel_thread_id("mygithub", "3/2")
    assert [call["thread_id"] for call in calls] == [issue, issue, pull, pull]
    # Each delivery is run once per thread.
    assert [call["delivery_key"] for call in calls] == ["d-1", "d-2", "d-3", "d-4"]
    assert calls[0]["binding"]["target"] == {"repository": "owner/repo", "number": 1}


def test_github_keys_events_without_a_conversation_by_delivery(
    ingress: SimpleNamespace, github_env: None
) -> None:
    channel = channels.github(on_event={"workflow_run.completed": lambda event, runtime: "prompt"})
    key = "workflow_run.completed"
    for delivery in ("d-1", "d-1", "d-2"):
        asyncio.run(github_send(channel, key, delivery=delivery))
    first, again, other = (call.kwargs for call in ingress.start.call_args_list)
    assert again["thread_id"] == first["thread_id"]
    assert other["thread_id"] != first["thread_id"]
    assert first["binding"]["target"] is None

    # A run for exactly one pull request continues that pull request.
    for_pull = github_payload(key)
    cast("dict[str, Any]", for_pull["workflow_run"])["pull_requests"] = [{"number": 2}]
    asyncio.run(github_send(channel, key, for_pull, delivery="d-3"))
    assert ingress.start.call_args.kwargs["thread_id"] == channel_thread_id("mygithub", "3/2")


def test_github_keys_actionless_events_by_name_and_maps_ci_runs(
    ingress: SimpleNamespace, github_env: None
) -> None:
    seen: list[GitHubEvent] = []

    def handler(event: GitHubEvent, runtime: object) -> str:
        seen.append(event)
        return "prompt"

    channel = channels.github(
        on_event={"push": handler, "status": handler, "check_run.completed": handler}
    )
    asyncio.run(github_send(channel, "push", delivery="d-push"))
    asyncio.run(github_send(channel, "status", delivery="d-status"))
    assert [(event.key, event.name, event.action) for event in seen] == [
        ("push", "push", None),
        ("status", "status", None),
    ]
    # A push with an action is not a push GitHub sends.
    odd = {**github_payload("push"), "action": "created"}
    asyncio.run(github_send(channel, "push", odd, delivery="d-odd"))
    assert len(seen) == 2

    check = github_payload("check_run.completed")
    cast("dict[str, Any]", check["check_run"])["pull_requests"] = [{"number": 2}]
    asyncio.run(github_send(channel, "check_run.completed", check, delivery="d-check"))
    assert ingress.start.call_args.kwargs["thread_id"] == channel_thread_id("mygithub", "3/2")


def test_github_reacts_to_an_edited_comment_like_a_new_one(
    ingress: SimpleNamespace, github_env: None
) -> None:
    channel = channels.github(on_event={"issue_comment.edited": lambda event, runtime: "prompt"})
    asyncio.run(github_send(channel, "issue_comment.edited"))
    target = ingress.start.call_args.kwargs["binding"]["target"]
    assert target == {"repository": "owner/repo", "number": 1}


def test_github_waits_for_a_busy_conversations_run_before_starting_the_next(
    ingress: SimpleNamespace, github_env: None
) -> None:
    # The active run may still stop for a decision, so the comment waits for it.
    channel = channels.github(on_event={"issue_comment.created": lambda event, runtime: "prompt"})
    ingress.start.side_effect = [
        {"skipped": "busy"},
        {"skipped": "busy"},
        {"thread_id": "thread-1", "run_id": "run-1"},
    ]

    async def run() -> int:
        response = await github_send(channel, "issue_comment.created")
        assert ingress.wait.await_count == 0
        await drain_github(ingress)
        return response.status_code

    assert asyncio.run(run()) == 202
    assert ingress.wait.await_count == 2
    assert ingress.start.await_count == 3
    assert ingress.observe.await_args.kwargs["run_id"] == "run-1"


def test_github_starts_nothing_for_a_redelivery(ingress: SimpleNamespace, github_env: None) -> None:
    handler = Mock(return_value="prompt")
    channel = channels.github(on_event={"issue_comment.created": handler})
    ingress.start.side_effect = [{"skipped": "duplicate"}]
    assert asyncio.run(github_send(channel, "issue_comment.created")).status_code == 202
    _drain(ingress)
    ingress.observe.assert_not_called()
    handler.assert_not_called()


def test_github_points_a_paused_conversation_to_studio_instead_of_starting_a_run(
    ingress: SimpleNamespace, github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _STUDIO_ENV.items():
        monkeypatch.setenv(key, value)
    api = _github_api(monkeypatch)
    handler = Mock(return_value="prompt")
    channel = channels.github(on_event={"issue_comment.created": handler})
    ingress.start.side_effect = [
        {"skipped": "paused", "assistant_id": _ASSISTANT, "interrupts": _PENDING}
    ]

    async def paused() -> int:
        response = await github_send(channel, "issue_comment.created")
        await drain_github(ingress)
        return response.status_code

    assert asyncio.run(paused()) == 202
    # The handler decides whether the event gets a notice; no run starts,
    # and it knows, so it can skip side effects.
    handler.assert_called_once()
    assert handler.call_args.args[1].interrupted is True
    assert handler.call_args.args[1].id == channel_thread_id("mygithub", "3/1")
    ingress.observe.assert_not_called()
    [(url, body)] = _posts(api)
    assert url == "https://api.github.com/repos/owner/repo/issues/1/comments"
    studio = _STUDIO.replace(_THREAD, channel_thread_id("mygithub", "3/1"))
    assert f"[LangSmith Studio]({studio})" in cast("dict[str, str]", body)["body"]


def test_github_paused_notice_links_the_runs_thread_in_studio(
    github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _STUDIO_ENV.items():
        monkeypatch.setenv(key, value)
    api = _github_api(monkeypatch)
    # No handler is asked again: it accepted the event that started the run.
    handler = Mock(return_value=None)
    channel = channels.github(on_event={"issue_comment.created": handler})
    assert channel.paused_notice is not None
    thread_id = channel_thread_id("mygithub", "3/1")

    asyncio.run(
        channel.paused_notice(
            {"repository": "octo/repo", "number": 1},
            {"thread_id": thread_id, "assistant_id": _ASSISTANT},
        )
    )

    [(_url, body)] = _posts(api)
    text = cast("dict[str, str]", body)["body"]
    assert text.startswith("I need a decision before I continue.")
    assert f"threadId={thread_id}" in text
    handler.assert_not_called()


def test_github_tells_on_finish_how_a_delivery_ended_after_its_notice(
    ingress: SimpleNamespace,
    github_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for key, value in _STUDIO_ENV.items():
        monkeypatch.setenv(key, value)
    api = _github_api(monkeypatch)
    calls: list[str] = []
    on_finish = AsyncMock(side_effect=lambda *_: calls.append(f"finish {len(_posts(api))}"))
    channel = channels.github(
        on_event={"issue_comment.created": lambda _e, _r: "prompt"}, on_finish=on_finish
    )
    ingress.observe.return_value = {
        "status": "interrupted",
        "assistant_id": _ASSISTANT,
        "interrupts": _PENDING,
    }

    async def run() -> None:
        await github_send(channel, "issue_comment.created")
        await drain_github(ingress)

    asyncio.run(run())
    # The run posts its own notice; the hook gets the same delivery as before.
    assert calls == ["finish 0"]
    event, thread, finished = on_finish.await_args.args
    assert event.key == "issue_comment.created"
    assert thread == GitHubChannelThread(
        id=channel_thread_id("mygithub", "3/1"), channel=ChannelName(name="mygithub")
    )
    assert finished == {
        "type": "run",
        "run": {"id": "run-1", "status": "interrupted"},
        "interrupts": _PENDING,
    }

    # A failing notice is logged; the user's hook still hears how it ended.
    monkeypatch.setattr(
        "managed_deepagents._channels.github._post_interrupted_notice",
        AsyncMock(side_effect=RuntimeError("GitHub down")),
    )
    ingress.start.side_effect = [{"skipped": "paused", "interrupts": _PENDING}]
    asyncio.run(run())
    assert on_finish.await_args.args[2] == {
        "type": "skipped",
        "reason": "waiting",
        "interrupts": _PENDING,
    }
    assert "channel finish notice failed (RuntimeError)" in caplog.text


def test_github_posts_no_paused_notice_for_an_event_its_handler_skips(
    ingress: SimpleNamespace, github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # E.g. an untrusted sender or a comment without a mention: no reply at all.
    for key, value in _STUDIO_ENV.items():
        monkeypatch.setenv(key, value)
    api = _github_api(monkeypatch)
    handler = Mock(return_value=None)
    channel = channels.github(on_event={"issue_comment.created": handler})
    ingress.start.side_effect = [
        {"skipped": "paused", "assistant_id": _ASSISTANT, "interrupts": _PENDING}
    ]

    async def paused() -> int:
        response = await github_send(channel, "issue_comment.created")
        await drain_github(ingress)
        return response.status_code

    assert asyncio.run(paused()) == 202
    handler.assert_called_once()
    assert _posts(api) == []


def test_github_starts_runs_with_what_agent_invoke_takes(
    ingress: SimpleNamespace, github_env: None
) -> None:
    results: list[object] = [
        HumanMessage("prompt"),
        HumanMessage(content=[{"type": "text", "text": "prompt"}]),
        {"messages": ["prompt"], "todos": []},
        (HumanMessage("prompt"), {"context": {"foo": "bar"}}),
    ]
    for result in results:
        channel = channels.github(on_event={"issues.opened": lambda _e, _r, value=result: value})
        asyncio.run(github_send(channel))
    human = {
        "role": "human",
        "content": "prompt",
        "additional_kwargs": {},
        "response_metadata": {},
    }
    assert ingress.prepared == [
        {"messages": [human], "state": {}},
        {
            "messages": [
                {
                    "role": "human",
                    "content": [{"type": "text", "text": "prompt"}],
                    "additional_kwargs": {},
                    "response_metadata": {},
                }
            ],
            "state": {},
        },
        {"messages": [human], "state": {"todos": []}},
        {"messages": [human], "state": {}, "context": {"foo": "bar"}},
    ]


def test_channel_thread_id_matches_the_npm_runtime() -> None:
    assert channel_thread_id("github", "42/1") == "c7123238-cf9b-5dd0-bd4c-ecc836e1d477"
    assert channel_thread_id("github", "42/1") != channel_thread_id("other", "42/1")


@pytest.mark.parametrize("result", [None, "", "  ", 42, {}, {"messages": []}])
def test_github_skip_or_invalid_return_creates_nothing(
    result: object, ingress: SimpleNamespace, github_env: None
) -> None:
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: result})

    async def run() -> None:
        assert (await github_send(channel)).status_code == 202
        await drain_github(ingress)

    asyncio.run(run())
    assert ingress.prepared == [None]
    ingress.observe.assert_not_called()


def test_github_unconfigured_and_own_bot_create_nothing(
    ingress: SimpleNamespace, github_env: None
) -> None:
    callback = AsyncMock(return_value="prompt")
    channel = channels.github(on_event={"issues.opened": callback})

    async def run() -> None:
        for key in ("issues.labeled", "issues.closed", "push.anything"):
            assert (await github_send(channel, key)).status_code == 202
        data = github_payload()
        data["sender"] = {"id": 99, "login": "app[bot]", "type": "Bot"}
        assert (await github_send(channel, data=data)).status_code == 202

    asyncio.run(run())
    assert not ingress.jobs
    callback.assert_not_called()
    ingress.start.assert_not_called()


@pytest.mark.parametrize("sender_type", ["Bot", "Organization"])
def test_github_runs_a_bot_or_organization_sender_as_the_channel(
    sender_type: str, ingress: SimpleNamespace, github_env: None
) -> None:
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})

    async def run() -> None:
        data = github_payload()
        data["sender"] = {"id": 8, "login": "automation", "type": sender_type}
        assert (await github_send(channel, data=data)).status_code == 202
        await drain_github(ingress)

    asyncio.run(run())
    assert ingress.start.call_args.kwargs["user_id"] == "mda:channel"


def test_github_awaits_callback_and_run_creation(
    ingress: SimpleNamespace, github_env: None
) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        pending = asyncio.Event()
        starting = asyncio.Event()
        start_entered = asyncio.Event()

        async def handler(event: object, runtime: object) -> str:
            entered.set()
            await pending.wait()
            return "ready"

        async def start(**kwargs: Any) -> dict[str, str]:
            await kwargs["prepare"]()
            start_entered.set()
            await starting.wait()
            return {"thread_id": kwargs["thread_id"], "run_id": "run-1"}

        ingress.start.side_effect = start
        channel = channels.github(on_event={"issues.opened": handler})
        task = asyncio.create_task(github_send(channel))
        await entered.wait()
        assert not task.done()
        pending.set()
        await start_entered.wait()
        assert not task.done()
        starting.set()
        assert (await task).status_code == 202
        await drain_github(ingress)

    asyncio.run(run())
    ingress.start.assert_called_once()


def test_github_callback_and_start_failures_are_safe(
    ingress: SimpleNamespace, github_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="mda")

    def failure(event: object, runtime: object) -> str:
        raise ValueError("private exception body")

    async def run() -> None:
        await github_send(channels.github(on_event={"issues.opened": failure}))
        await drain_github(ingress)
        assert ingress.prepared == [None]

        async def start(**kwargs: Any) -> None:
            await kwargs["prepare"]()
            raise ValueError("upstream sensitive error")

        ingress.start.side_effect = start
        response = await github_send(
            channels.github(on_event={"issues.opened": lambda event, runtime: "sensitive prompt"})
        )
        assert response.status_code == 500
        await drain_github(ingress)

    asyncio.run(run())
    ingress.observe.assert_not_called()
    for value in (
        "private exception body",
        "upstream sensitive error",
        "sensitive prompt",
        "private payload",
    ):
        assert value not in caplog.text
    assert "delivery-1" in caplog.text
    assert "channel run could not be started" in caplog.text
    assert "GitHub handler returned run input" in caplog.text
    assert "github accepted" not in caplog.text
    assert "channel run started" not in caplog.text


@pytest.mark.parametrize("bot_id", ["", "abc", "0", "-1", "1.5", "9007199254740992"])
def test_github_invalid_bot_config_fails_closed(
    bot_id: str, ingress: SimpleNamespace, github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_APP_BOT_ID", bot_id)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    assert asyncio.run(github_send(channel)).status_code == 400
    assert not ingress.jobs


def test_github_invalid_envelopes(ingress: SimpleNamespace, github_env: None) -> None:
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})

    async def run() -> None:
        for data in (
            {"action": "opened"},
            {**github_payload(), "installation": None},
            {**github_payload(), "sender": {"id": "7"}},
            {**github_payload(), "issue": {"title": "missing fields"}},
        ):
            assert (await github_send(channel, data=data)).status_code == 400
        assert (await github_send(channel, delivery="")).status_code == 400

    asyncio.run(run())
    assert not ingress.jobs


def test_github_preserves_original_event(ingress: SimpleNamespace, github_env: None) -> None:
    def handler(event: object, runtime: object) -> str:
        event.payload["issue"]["title"] = "changed by callback"
        return "prompt"

    async def run() -> None:
        await github_send(channels.github(on_event={"issues.opened": handler}))
        await drain_github(ingress)

    asyncio.run(run())
    assert (
        ingress.start.call_args.kwargs["binding"]["raw_event"]["payload"]["issue"]["title"]
        == "untrusted title"
    )


@pytest.mark.parametrize("key", GITHUB_KEYS)
def test_github_dispatch_requires_exact_action(key: str) -> None:
    data = github_payload(key)
    github_event(key, data, "delivery-1")
    data["action"] = "unrelated"
    with pytest.raises(ValueError, match="GitHub action does not match event key"):
        github_event(key, data, "delivery-1")
    assert frozenset(GITHUB_KEYS) == SUPPORTED_EVENT_KEYS


@pytest.mark.parametrize("value", ["7", True, 0, -1, 9007199254740992, None, 1.5])
def test_github_dispatch_ids_are_strict(value: object) -> None:
    for subject, field in (
        ("sender", "id"),
        ("installation", "id"),
        ("repository", "id"),
        ("issue", "number"),
    ):
        data = github_payload()
        data[subject][field] = value
        with pytest.raises(ValidationError):
            github_event("issues.opened", data, "delivery-1")


@pytest.mark.parametrize(
    ("key", "subject", "field"),
    [
        ("issues.opened", "issue", "body"),
        ("pull_request.closed", "pull_request", "body"),
        ("pull_request_review.submitted", "review", "body"),
        ("workflow_run.completed", "workflow_run", "conclusion"),
    ],
)
def test_github_required_nullable_fields(key: str, subject: str, field: str) -> None:
    data = github_payload(key)
    data[subject][field] = None
    github_event(key, data, "delivery-1")
    data[subject][field] = 42
    with pytest.raises(ValidationError):
        github_event(key, data, "delivery-1")
    del data[subject][field]
    with pytest.raises(ValidationError):
        github_event(key, data, "delivery-1")


def test_github_optional_boolean_is_not_nullable_or_coerced() -> None:
    data = github_payload("pull_request.opened")
    del data["pull_request"]["draft"]
    github_event("pull_request.opened", data, "delivery-1")
    for value in (None, "false", 0):
        data["pull_request"]["draft"] = value
        with pytest.raises(ValidationError):
            github_event("pull_request.opened", data, "delivery-1")


@pytest.mark.parametrize("key", GITHUB_KEYS)
def test_github_preserves_nested_unknown_fields(
    key: str, ingress: SimpleNamespace, github_env: None
) -> None:
    seen = []
    data = github_payload(key)
    data["extra"] = {"nested": ["keep", 1]}
    for value in data.values():
        if isinstance(value, dict):
            value["extra"] = {"nested": ["preserved"]}
    data["repository"]["owner"]["extra"] = {"permissions": ["read"]}

    def handler(event: object, runtime: object) -> None:
        seen.append(event.payload)

    async def run() -> None:
        assert (
            await github_send(channels.github(on_event={key: handler}), key, data)
        ).status_code == 202
        await drain_github(ingress)

    asyncio.run(run())
    assert seen == [data]
    ingress.observe.assert_not_called()


def test_github_unconfigured_events_use_only_receipt_schema(
    ingress: SimpleNamespace, github_env: None
) -> None:
    data = {
        "action": "opened",
        "issue": {"number": 1},
        "sender": None,
        "installation": "not a dispatch envelope",
    }

    async def run() -> None:
        unconfigured = channels.github(on_event={"issues.labeled": lambda event, runtime: "prompt"})
        assert (await github_send(unconfigured, data=data)).status_code == 202
        assert not ingress.jobs
        configured = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
        assert (await github_send(configured, data=data)).status_code == 400
        assert not ingress.jobs

    asyncio.run(run())


def test_github_async_callback_can_skip(ingress: SimpleNamespace, github_env: None) -> None:
    async def handler(event: object, runtime: object) -> None:
        await asyncio.sleep(0)

    async def run() -> None:
        assert (
            await github_send(channels.github(on_event={"issues.opened": handler}))
        ).status_code == 202
        await drain_github(ingress)

    asyncio.run(run())
    assert ingress.prepared == [None]
    ingress.observe.assert_not_called()


@pytest.mark.parametrize(
    ("source", "name"), [("channels/reviewer.py", "reviewer"), (None, "github")]
)
def test_github_callback_uses_mounted_name(
    source: str | None, name: str, ingress: SimpleNamespace, github_env: None
) -> None:
    seen = []

    def handler(event: object, thread: object) -> None:
        seen.append(thread.channel.name)

    channel = channels.github(on_event={"issues.opened": handler})
    declarations = (
        collect_channels([(source, SimpleNamespace(channel=channel))]) if source else [channel]
    )
    app = build_managed_http_app(channels=declarations)
    raw = json.dumps(github_payload()).encode()
    status, _, _ = _call(
        app,
        "POST",
        f"/channels/{name}/events",
        body=raw,
        headers={
            "x-github-event": "issues",
            "x-github-delivery": "delivery-1",
            "x-hub-signature-256": "sha256=" + hmac.new(b"test-secret", raw, "sha256").hexdigest(),
        },
    )
    assert status == 202
    assert seen == [name]
    ingress.observe.assert_not_called()


def test_http_request_keeps_original_shape() -> None:
    request = Request({"type": "http", "method": "POST", "path": "/events", "headers": []})
    ctx = HttpChannelRequest(request=request, raw_body=b"{}")
    assert ctx.request is request
    assert ctx.raw_body == b"{}"
    assert {field.name for field in fields(ctx)} == {"request", "raw_body"}


def test_github_publishes_handled_events_for_deploy() -> None:
    channel = channels.github(
        on_event={
            "issues.opened": lambda _event, _runtime: "triage",
            "issue_comment.created": lambda _event, _runtime: None,
        }
    )
    assert channel.requirements("github")["providerConfig"] == {
        "events": ["issue_comment.created", "issues.opened"],
        "webhookEvents": ["issue_comment", "issues"],
        "permissions": [
            {
                "name": "issues",
                "level": "write",
                "events": ["issue_comment.created", "issues.opened"],
            },
            {"name": "metadata", "level": "read", "events": []},
        ],
    }
    assert channels.github().requirements("github")["providerConfig"] == {
        "events": [],
        "webhookEvents": [],
        "permissions": [{"name": "metadata", "level": "read", "events": []}],
    }


def test_github_publishes_app_options_for_deploy() -> None:
    channel = channels.github(
        app={
            "permissions": {"contents": "read"},
            "repositories": ["acme/api"],
        },
        on_event={"issues.opened": lambda _event, _runtime: "triage"},
    )
    assert channel.requirements("github")["providerConfig"] == {
        "events": ["issues.opened"],
        "webhookEvents": ["issues"],
        "permissions": [
            {"name": "contents", "level": "read", "events": []},
            {"name": "issues", "level": "write", "events": ["issues.opened"]},
            {"name": "metadata", "level": "read", "events": []},
        ],
        "repositories": ["acme/api"],
    }


def test_github_starts_nothing_for_repositories_outside_the_channel(
    ingress: SimpleNamespace, github_env: None
) -> None:
    seen: list[object] = []

    def handler(event: object, _runtime: object) -> str:
        seen.append(event)
        return "triage"

    elsewhere = channels.github(
        app={"repositories": ["acme/api"]}, on_event={"issues.opened": handler}
    )
    with pytest.raises(ValueError, match="at least one repository"):
        channels.github(app={"repositories": []}, on_event={"issues.opened": handler})

    # Repository names compare case-insensitively.
    listed = channels.github(
        app={"repositories": [" Owner/Repo "]}, on_event={"issues.opened": handler}
    )

    async def run() -> None:
        assert (await github_send(elsewhere)).status_code == 202
        assert seen == []
        ingress.start.assert_not_called()
        assert (await github_send(listed)).status_code == 202
        assert len(seen) == 1

    asyncio.run(run())


def test_github_reads_app_values_from_agent_auth_when_env_is_unset(
    ingress: SimpleNamespace, github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from managed_deepagents._channels import github as github_module  # noqa: PLC0415
    from managed_deepagents._connections import connections as runtime_connections  # noqa: PLC0415

    monkeypatch.delenv("GH_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("GH_APP_BOT_ID", raising=False)
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "dep-1")
    github_module.reset_github_app_values()
    reads: list[str] = []

    async def resolve(slug: str) -> str:
        reads.append(slug)
        return "test-secret" if slug.endswith("/webhook-secret") else "99"

    monkeypatch.setattr(runtime_connections, "resolve_deployment_secret", resolve)
    try:
        test_github_dispatches_supported_key("issues.opened", ingress, github_env)
        assert sorted(set(reads)) == [
            "mda/github-app/dep-1/bot-id",
            "mda/github-app/dep-1/webhook-secret",
        ]
        assert len(reads) == 2
    finally:
        github_module.reset_github_app_values()


@pytest.fixture
def agent_auth_github(
    github_env: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[Callable[[str], Awaitable[str]]], list[str]]]:
    """Read the GitHub App values through a stubbed Agent Auth lookup."""
    from managed_deepagents._channels import github as github_module  # noqa: PLC0415
    from managed_deepagents._connections import connections as runtime_connections  # noqa: PLC0415

    monkeypatch.delenv("GH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("LANGSMITH_HOST_PROJECT_ID", "dep-1")
    github_module.reset_github_app_values()

    def stub(read: Callable[[str], Awaitable[str]]) -> list[str]:
        reads: list[str] = []

        async def resolve(slug: str) -> str:
            reads.append(slug)
            return await read(slug)

        monkeypatch.setattr(runtime_connections, "resolve_deployment_secret", resolve)
        return reads

    yield stub
    github_module.reset_github_app_values()


def test_github_remembers_a_missing_webhook_secret(
    ingress: SimpleNamespace, agent_auth_github: Callable[..., list[str]]
) -> None:
    from managed_deepagents._connections import ConnectionNotFoundError  # noqa: PLC0415

    async def missing(slug: str) -> str:
        raise ConnectionNotFoundError(slug, "agent")

    reads = agent_auth_github(missing)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    assert asyncio.run(github_send(channel)).status_code == 401
    assert asyncio.run(github_send(channel)).status_code == 401
    assert reads == ["mda/github-app/dep-1/webhook-secret"]
    assert not ingress.jobs


def test_github_answers_the_ping_before_the_webhook_secret_is_stored(
    ingress: SimpleNamespace, agent_auth_github: Callable[..., list[str]]
) -> None:
    from managed_deepagents._connections import ConnectionNotFoundError  # noqa: PLC0415

    async def missing(slug: str) -> str:
        raise ConnectionNotFoundError(slug, "agent")

    reads = agent_auth_github(missing)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    ping = github_send(channel, "ping", {"zen": "Encourage flow."}, signature="")
    assert asyncio.run(ping).status_code == 200
    assert reads == []
    # The secret was never remembered as missing, so the first event reads it.
    assert asyncio.run(github_send(channel)).status_code == 401
    assert reads == ["mda/github-app/dep-1/webhook-secret"]
    assert not ingress.jobs


def test_github_fails_verification_when_agent_auth_is_unavailable(
    ingress: SimpleNamespace, agent_auth_github: Callable[..., list[str]]
) -> None:
    async def unavailable(slug: str) -> str:
        raise RuntimeError("credential store down")

    reads = agent_auth_github(unavailable)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    response = asyncio.run(github_send(channel))
    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "channel verification failed"}
    # Failures are not cached: the next delivery reads again.
    assert asyncio.run(github_send(channel)).status_code == 500
    assert len(reads) == 2
    assert not ingress.jobs


@pytest.mark.parametrize(
    ("delivery", "logged"),
    [
        ("a3e3c8fc-c8ab-11f1-80da-129938ed2204", "a3e3c8fc-c8ab-11f1-80da-129938ed2204"),
        # Anything else could forge a log line.
        ("x\nforged", "unknown"),
    ],
)
def test_github_says_why_a_delivery_cannot_be_verified(
    ingress: SimpleNamespace,
    agent_auth_github: Callable[..., list[str]],
    caplog: pytest.LogCaptureFixture,
    delivery: str,
    logged: str,
) -> None:
    from managed_deepagents._agent_auth_http import (  # noqa: PLC0415
        ConnectionsNotConfiguredError,
    )

    async def unconfigured(slug: str) -> str:
        raise ConnectionsNotConfiguredError("LANGSMITH_API_KEY")

    agent_auth_github(unconfigured)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    response = asyncio.run(github_send(channel, delivery=delivery))

    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "channel verification failed"}
    assert (
        f"GitHub delivery {logged} cannot be verified: "
        "connections are not configured: LANGSMITH_API_KEY is not set"
    ) in caplog.text
    assert "forged" not in caplog.text
    assert not ingress.jobs


def test_github_keeps_agent_auth_failures_out_of_the_logs(
    ingress: SimpleNamespace,
    agent_auth_github: Callable[..., list[str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def unavailable(slug: str) -> str:
        raise RuntimeError("upstream body with secret material")

    agent_auth_github(unavailable)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    assert asyncio.run(github_send(channel)).status_code == 500
    assert "secret material" not in caplog.text
    assert "cannot be verified" not in caplog.text


def test_github_rejects_malformed_signatures_without_reading_agent_auth(
    ingress: SimpleNamespace, agent_auth_github: Callable[..., list[str]]
) -> None:
    async def secret(slug: str) -> str:
        return "test-secret"

    reads = agent_auth_github(secret)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})
    for signature in ("", "sha1=abc", "sha256=" + "z" * 64):
        response = asyncio.run(github_send(channel, signature=signature))
        assert response.status_code == 401
    assert reads == []


def test_github_concurrent_first_deliveries_share_one_read(
    ingress: SimpleNamespace, agent_auth_github: Callable[..., list[str]]
) -> None:
    async def slow_secret(slug: str) -> str:
        await asyncio.sleep(0.01)
        return "test-secret" if slug.endswith("/webhook-secret") else "99"

    reads = agent_auth_github(slow_secret)
    channel = channels.github(on_event={"issues.opened": lambda event, runtime: "prompt"})

    async def run() -> list[int]:
        responses = await asyncio.gather(
            *(github_send(channel, delivery=f"delivery-{i}") for i in range(3))
        )
        return [response.status_code for response in responses]

    assert asyncio.run(run()) == [202, 202, 202]
    assert reads == ["mda/github-app/dep-1/webhook-secret"]


def _ingress_start(monkeypatch: pytest.MonkeyPatch, read: dict[str, object]) -> SimpleNamespace:
    start = AsyncMock(return_value={"thread_id": "t-1", "run_id": "r-1"})
    monkeypatch.setattr(http_ingress, "_has_loopback_credential", lambda: True)
    monkeypatch.setattr(http_ingress, "_create_ingress_loopback_client", lambda **kwargs: object())
    monkeypatch.setattr(http_ingress, "ingress_thread_state", AsyncMock(return_value=read))
    monkeypatch.setattr(http_ingress, "start_ingress_run", start)

    async def deliver(**kwargs: Any) -> object:
        return await http_ingress.start_http_ingress_agent(
            user_id="mda:channel",
            source_thread_key="http:v1:key",
            binding={
                "name": "github",
                "provider": "github",
                "source_thread_key": "http:v1:key",
                "delivery_id": "delivery-1",
                "transport": "direct",
                "target": None,
                "event": {"type": "message", "messages": []},
            },
            thread_id="t-1",
            assistant_id="agent",
            source_provider="github",
            delivery_key="d-9",
            **kwargs,
        )

    return SimpleNamespace(deliver=deliver, start=start)


def test_a_paused_thread_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    ingress = _ingress_start(
        monkeypatch, {"state": "paused", "assistant_id": "a-1", "interrupts": _PENDING}
    )
    prepare = AsyncMock()
    assert asyncio.run(ingress.deliver(prepare=prepare)) == {
        "skipped": "paused",
        "assistant_id": "a-1",
        "interrupts": _PENDING,
    }
    prepare.assert_not_awaited()
    ingress.start.assert_not_awaited()


def test_the_run_input_is_asked_for_only_when_the_thread_takes_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = {
        "messages": [{"role": "human", "content": "prompt"}],
        "state": {"todos": []},
        "context": {"foo": "bar"},
    }
    prepare = AsyncMock(return_value=prepared)
    busy = _ingress_start(monkeypatch, {"state": "busy"})
    assert asyncio.run(busy.deliver(prepare=prepare)) == {"skipped": "busy"}
    prepare.assert_not_awaited()

    ready = _ingress_start(monkeypatch, {"state": "ready"})
    assert asyncio.run(ready.deliver(prepare=prepare)) == {"thread_id": "t-1", "run_id": "r-1"}
    kwargs = ready.start.await_args.kwargs
    assert kwargs["messages"] == prepared["messages"]
    assert kwargs["state"] == {"todos": []}
    assert kwargs["context"]["foo"] == "bar"
    assert kwargs["context"]["mda_channel"]["event"] == {
        "type": "message",
        "messages": prepared["messages"],
    }
    assert asyncio.run(ready.deliver(prepare=AsyncMock(return_value=None))) == {
        "skipped": "ignored"
    }


_WORKSPACE = "11111111-1111-4111-8111-111111111111"
_DEPLOYMENT = "22222222-2222-4222-8222-222222222222"
_THREAD = "33333333-3333-4333-8333-333333333333"
_ASSISTANT = "44444444-4444-4444-8444-444444444444"
_STUDIO = (
    f"https://smith.langchain.com/o/{_WORKSPACE}/a/acme-triage/studio/thread"
    f"?organizationId={_WORKSPACE}&render=interact&hostProjectId={_DEPLOYMENT}"
    f"&assistantId={_ASSISTANT}&mode=chat&threadId={_THREAD}"
)


def test_github_links_to_the_thread_in_the_environment_deploy_recorded() -> None:
    env = {
        "LANGSMITH_TENANT_ID": _WORKSPACE,
        "LANGSMITH_HOST_PROJECT_ID": _DEPLOYMENT,
        "LANGSMITH_DEPLOYMENT_NAME": "acme-triage",
        "MDA_LANGSMITH_URL": "https://smith.langchain.com",
    }
    run: HttpDelivery = {"thread_id": _THREAD, "assistant_id": _ASSISTANT}
    assert studio_url(run, env) == _STUDIO
    assert studio_url(run, {**env, "MDA_LANGSMITH_URL": "http://localhost:21980"}) == (
        _STUDIO.replace("https://smith.langchain.com", "http://localhost:21980")
    )
    # Without the recorded environment there is nothing to link to, whatever
    # endpoint the runtime talks to.
    unrecorded = {key: value for key, value in env.items() if key != "MDA_LANGSMITH_URL"}
    assert (
        studio_url(run, {**unrecorded, "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com"})
        is None
    )
    assert studio_url(run, {**env, "MDA_LANGSMITH_URL": "javascript:alert(1)"}) is None
    # Without the assistant or the deployment's name, the deployment's page.
    page = f"/o/{_WORKSPACE}/host/deployments/{_DEPLOYMENT}"
    assert studio_url({"thread_id": _THREAD}, env) == f"https://smith.langchain.com{page}"
    assert studio_url(run, {**env, "LANGSMITH_TENANT_ID": "../evil"}) is None
    assert (
        studio_url(run, {**env, "LANGSMITH_DEPLOYMENT_NAME": "a/../b"})
        == f"https://smith.langchain.com{page}"
    )


def test_studio_links_open_a_graphs_default_assistant() -> None:
    env = {
        "LANGSMITH_TENANT_ID": _WORKSPACE,
        "LANGSMITH_HOST_PROJECT_ID": _DEPLOYMENT,
        "LANGSMITH_DEPLOYMENT_NAME": "acme-triage",
        "MDA_LANGSMITH_URL": "https://smith.langchain.com",
    }
    # Channels start runs by graph ID; the Agent Server derives the assistant.
    assert studio_url({"thread_id": _THREAD, "assistant_id": "agent"}, env) == _STUDIO.replace(
        _ASSISTANT, "fe096781-5601-53d2-b2f6-0d3403f7e9ca"
    )
    assert studio_url({"thread_id": _THREAD, "assistant_id": "a/../b"}, env) == (
        f"https://smith.langchain.com/o/{_WORKSPACE}/host/deployments/{_DEPLOYMENT}"
    )


def _github_api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """GitHub as the App sees it: what the App posts."""
    from managed_deepagents._channels import github as github_module  # noqa: PLC0415
    from managed_deepagents._connections import connections as runtime_connections  # noqa: PLC0415

    resolve = AsyncMock(return_value="ghs_installation")
    requests: list[httpx.Request] = []

    def github_api(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(runtime_connections, "resolve_deployment_secret", resolve)
    monkeypatch.setattr(
        github_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(github_api), **kwargs),
    )
    return SimpleNamespace(requests=requests, resolve=resolve)


_STUDIO_ENV = {
    "LANGSMITH_TENANT_ID": _WORKSPACE,
    "LANGSMITH_HOST_PROJECT_ID": _DEPLOYMENT,
    "LANGSMITH_DEPLOYMENT_NAME": "acme-triage",
    "MDA_LANGSMITH_URL": "https://smith.langchain.com",
}


def _posts(api: SimpleNamespace) -> list[tuple[str, object]]:
    return [
        (str(request.url), json.loads(request.content))
        for request in api.requests
        if request.method == "POST"
    ]


def test_github_on_received_hears_each_handled_delivery_before_on_finish(
    ingress: SimpleNamespace, github_env: None
) -> None:
    calls: list[str] = []
    seen: list[tuple[object, object]] = []

    def on_received(event: GitHubEvent, thread: GitHubChannelThread) -> None:
        calls.append("received")
        seen.append((event, thread))

    async def on_finish(*_: object) -> None:
        calls.append("finish")

    channel = channels.github(
        on_event={"issue_comment.created": lambda _e, _t: "prompt"},
        on_received=on_received,
        on_finish=on_finish,
    )

    async def run() -> None:
        await github_send(channel, "issue_comment.created", delivery="d-1")
        # An event nothing handles reaches no thread, so the hook doesn't hear it.
        await github_send(channel, "issues.opened", delivery="d-2")
        await drain_github(ingress)

    asyncio.run(run())
    assert calls == ["received", "finish"]
    [(event, thread)] = seen
    assert isinstance(event, GitHubEvent)
    assert event.key == "issue_comment.created"
    assert event.delivery_id == "d-1"
    assert event.payload == github_payload("issue_comment.created")
    assert thread == GitHubChannelThread(
        id=channel_thread_id("mygithub", "3/1"), channel=ChannelName(name="mygithub")
    )
    ingress.start.assert_called_once()


def test_github_on_received_fires_before_mda_reads_the_thread(
    ingress: SimpleNamespace, github_env: None
) -> None:
    # Even when the thread then waits on a decision, or the handler declines.
    on_received = AsyncMock()
    channel = channels.github(
        on_event={"issue_comment.created": lambda _e, _t: None}, on_received=on_received
    )
    ingress.start.side_effect = [{"skipped": "paused", "interrupts": _PENDING}]

    async def run() -> None:
        await github_send(channel, "issue_comment.created", delivery="d-1")
        await github_send(channel, "issue_comment.created", delivery="d-2")
        await drain_github(ingress)

    asyncio.run(run())
    assert on_received.await_count == 2


def test_github_failing_on_received_is_logged_and_the_run_starts(
    ingress: SimpleNamespace, github_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    def on_received(_event: object, _thread: object) -> None:
        raise RuntimeError("private reaction failure")

    channel = channels.github(
        on_event={"issue_comment.created": lambda _e, _t: "prompt"}, on_received=on_received
    )

    async def run() -> None:
        response = await github_send(channel, "issue_comment.created")
        assert response.status_code == 202
        await drain_github(ingress)

    asyncio.run(run())
    ingress.start.assert_called_once()
    ingress.observe.assert_called_once()
    assert "channel received notice failed (RuntimeError)" in caplog.text
    assert "private reaction failure" not in caplog.text


_MENTION_CASES = json.loads(
    (Path(__file__).parents[2] / "testdata" / "github-mentions.json").read_text()
)


def test_github_endpoints_are_sent_the_handles_an_event_mentions(
    ingress: SimpleNamespace, github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from managed_deepagents._channels.http import endpoint as endpoint_module  # noqa: PLC0415

    sent: list[dict[str, Any]] = []
    real_client = httpx.AsyncClient

    def service(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    monkeypatch.setattr(
        endpoint_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(service), **kwargs),
    )
    channel = channels.github(on_event={"issue_comment.created": "https://backend.example/gh"})
    data = github_payload("issue_comment.created")
    data["comment"] = {**cast("dict[str, object]", data["comment"]), "body": "@OpenSWE fix it"}

    async def run() -> None:
        await github_send(channel, "issue_comment.created", data)
        await drain_github(ingress)

    asyncio.run(run())
    [request] = sent
    # As a handler gets it, from the channel's one list of fields; the same JSON
    # the TypeScript runtime sends.
    assert request["event"] == {
        "key": "issue_comment.created",
        "deliveryId": "delivery-1",
        "payload": data,
        "action": "created",
        "repository": {
            "id": data["repository"]["id"],
            "owner": data["repository"]["owner"]["login"],
            "name": data["repository"]["name"],
            "fullName": data["repository"]["full_name"],
            "private": data["repository"]["private"],
        },
        "sender": {
            "id": data["sender"]["id"],
            "login": data["sender"]["login"],
            "type": data["sender"]["type"],
        },
        "actor": {
            "provider": "github",
            "id": str(data["sender"]["id"]),
            "name": data["sender"]["login"],
            "type": data["sender"]["type"],
        },
        "mentions": ["@openswe"],
    }


@pytest.mark.parametrize(
    "case", _MENTION_CASES["texts"], ids=[case["text"][:40] for case in _MENTION_CASES["texts"]]
)
def test_github_mentions_read_text_as_github_does(case: dict[str, Any]) -> None:
    data = github_payload("issue_comment.created")
    data["comment"] = {"id": 4, "body": case["text"]}
    event = github_event("issue_comment.created", data, "delivery-1")
    assert event.mentions == tuple(case["mentions"])


@pytest.mark.parametrize(
    "case", _MENTION_CASES["events"], ids=[case["name"] for case in _MENTION_CASES["events"]]
)
def test_github_mentions_come_only_from_text_the_event_wrote(case: dict[str, Any]) -> None:
    data = github_payload(case["key"])
    for field, values in case["patch"].items():
        data[field] = {**cast("dict[str, object]", data[field]), **values}
    if "changes" in case:
        data["changes"] = case["changes"]
    assert github_event(case["key"], data, "delivery-1").mentions == tuple(case["mentions"])


def test_github_handlers_get_the_mentions_and_github_s_payload(
    ingress: SimpleNamespace, github_env: None
) -> None:
    handler = Mock(return_value=None)
    channel = channels.github(on_event={"issue_comment.created": handler, "push": handler})
    data = github_payload("issue_comment.created")
    data["comment"] = {"id": 4, "body": "Could @OpenSWE look?"}
    asyncio.run(github_send(channel, "issue_comment.created", data, delivery="d-1"))
    asyncio.run(github_send(channel, "push", delivery="d-2"))
    comment, push = (call.args[0] for call in handler.call_args_list)
    assert comment.mentions == ("@openswe",)
    # The payload stays GitHub's own.
    assert comment.payload == data
    assert push.mentions == ()


def test_github_handler_sees_mentions_while_the_thread_waits(
    ingress: SimpleNamespace, github_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A handler that answers only mentions decides the waiting notice the same way.
    for name, value in _STUDIO_ENV.items():
        monkeypatch.setenv(name, value)
    api = _github_api(monkeypatch)
    seen: list[tuple[str, ...]] = []

    def handler(event: GitHubEvent, _thread: object) -> str | None:
        seen.append(event.mentions)
        return "go" if "@openswe" in event.mentions else None

    channel = channels.github(on_event={"issue_comment.created": handler})
    ingress.start.side_effect = lambda *_, **__: {
        "skipped": "paused",
        "assistant_id": _ASSISTANT,
        "interrupts": _PENDING,
    }
    key = "issue_comment.created"
    quiet = github_payload(key)
    loud = github_payload(key)
    loud["comment"] = {"id": 5, "body": "@openswe go"}

    async def run() -> None:
        await github_send(channel, key, quiet, delivery="d-1")
        await github_send(channel, key, loud, delivery="d-2")
        await drain_github(ingress)

    asyncio.run(run())
    assert seen == [(), ("@openswe",)]
    assert len(_posts(api)) == 1
