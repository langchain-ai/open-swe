"""The status a Slack channel shows while a run works."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from managed_deepagents import channels
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._channels.slack import status as status_module
from managed_deepagents._channels.slack.status import (
    SlackStatusMiddleware,
    slack_status_choosers,
    slack_status_fields,
    started_status,
)


def test_sends_a_line_on_one_line_at_most_256_characters() -> None:
    assert slack_status_fields("  is  reading\nthe thread ") == {
        "message": {"text": "is reading the thread"}
    }
    fields = slack_status_fields("x" * 300)
    assert fields is not None
    assert len(fields["message"]["text"]) == 256


def test_sends_steps_pending_unless_they_say_otherwise() -> None:
    assert slack_status_fields(
        ["Read the issue", {"title": "Search", "status": "in_progress"}, {"title": "Answer"}]
    ) == {
        "steps": [
            {"title": "Read the issue", "status": "pending"},
            {"title": "Search", "status": "in_progress"},
            {"title": "Answer", "status": "pending"},
        ]
    }


def test_keeps_the_first_50_steps() -> None:
    fields = slack_status_fields([f"Step {index}" for index in range(60)])
    assert fields is not None
    assert len(fields["steps"]) == 50


@pytest.mark.parametrize(
    "value",
    [
        "   ",
        ["Read", " "],
        [{"title": "Read", "status": "done"}],
        [{"title": "Read", "status": ["done"]}],
        42,
        {"text": "Reading"},
    ],
    ids=["blank-line", "blank-step", "unknown-status", "unhashable-status", "number", "mapping"],
)
def test_rejects_what_is_not_a_status(value: object) -> None:
    assert slack_status_fields(value) is None


def test_started_status() -> None:
    assert started_status(None) is None
    assert started_status("is on it") == {"display": "plan", "message": {"text": "is on it"}}
    assert started_status(["Read"]) == {
        "display": "plan",
        "steps": [{"title": "Read", "status": "pending"}],
    }
    # A chooser is called by the run; `run.started` only picks the plan.
    assert started_status(lambda context: "is on it") == {"display": "plan"}


@pytest.mark.parametrize("value", ["is on it", ["Read", {"title": "Answer"}], lambda context: None])
def test_channel_accepts_a_status(value: object) -> None:
    assert channels.slack(status=value).status is value  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", [{"title": "Read", "status": "done"}], 1])
def test_channel_rejects_what_is_not_a_status(value: object) -> None:
    with pytest.raises(ValueError, match="must be a status line"):
        channels.slack(status=value)  # type: ignore[arg-type]


def test_only_a_chooser_needs_the_middleware() -> None:
    fixed = channels.slack(status="is on it")
    fixed.name = "fixed"
    chooser = channels.slack(status=lambda context: None)
    chooser.name = "team"
    assert list(slack_status_choosers([fixed, chooser])) == ["team"]


class _Client:
    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._delay = delay
        self._fail = fail

    async def post_runtime_event(self, channel_id: str, body: dict[str, Any]) -> dict[str, str]:
        await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("down")
        self.events.append((channel_id, body))
        return {"status": "handled"}


def _context() -> dict[str, object]:
    return {
        "mda_channel": {
            "name": "team",
            "provider": "slack",
            "transport": "trigger_server",
            "sourceThreadKey": "source",
            "deliveryId": "delivery-1",
            "target": {
                "channel_id": "install",
                "source_thread_key": "source",
                "address": {"conversation_id": "C123", "thread_id": "171.1"},
            },
            "event": {"type": "message", "messages": []},
        }
    }


def _config(
    *, provider: str = "slack", run_id: str = "run-1", namespace: str = "model:1"
) -> dict[str, object]:
    return {
        "configurable": {
            "thread_id": "thread-1",
            "checkpoint_ns": namespace,
            "langgraph_auth_user": {
                "mda_source_provider": provider,
                "mda_source_thread_id": "source",
            },
        },
        "metadata": {"run_id": run_id},
    }


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """A Slack run whose channel has a status chooser, with quick timings."""
    channel = channels.slack()
    channel.name = "team"
    register_channel_messaging([channel])
    client = _Client()
    state = SimpleNamespace(client=client, config=_config())
    monkeypatch.setattr(status_module, "get_config", lambda: state.config)
    monkeypatch.setattr(status_module, "create_trigger_client", lambda: state.client)
    monkeypatch.setattr(status_module, "_MIN_UPDATE_GAP_S", 0.05)
    monkeypatch.setattr(status_module, "_HEARTBEAT_S", 0.05)
    monkeypatch.setattr(status_module, "_CHOOSER_TIMEOUT_S", 0.05)
    yield state
    register_channel_messaging([])


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(context=_context())


def _state(text: str) -> dict[str, object]:
    return {"messages": [text]}


def _middleware(chooser: Callable[[Any], object]) -> SlackStatusMiddleware:
    return SlackStatusMiddleware({"team": chooser})  # type: ignore[dict-item]


async def _settle(seconds: float = 0.01) -> None:
    await asyncio.sleep(seconds)


def test_posts_what_the_chooser_says(run: SimpleNamespace) -> None:
    seen: list[object] = []

    def chooser(context: object) -> str:
        seen.append(context)
        return "is reading the thread"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        await middleware.abefore_agent(_state("hello"), _runtime())  # type: ignore[arg-type]
        await _settle()

    asyncio.run(scenario())
    assert seen == [{"state": _state("hello")}]
    [(channel_id, event)] = run.client.events
    assert channel_id == "install"
    assert event == {
        "id": event["id"],
        "type": "run.thinking",
        "delivery_id": "delivery-1",
        "source_thread_key": "source",
        "address": {"conversation_id": "C123", "thread_id": "171.1"},
        "run_thread_id": "thread-1",
        "run_id": "run-1",
        "display": "plan",
        "message": {"text": "is reading the thread"},
    }


def test_posts_steps_from_an_async_chooser(run: SimpleNamespace) -> None:
    async def chooser(context: object) -> list[object]:
        return ["Read", {"title": "Answer", "status": "in_progress"}]

    async def scenario() -> None:
        middleware = _middleware(chooser)
        await middleware.abefore_agent(_state("hello"), _runtime())  # type: ignore[arg-type]
        await _settle()

    asyncio.run(scenario())
    assert run.client.events[-1][1]["steps"] == [
        {"title": "Read", "status": "pending"},
        {"title": "Answer", "status": "in_progress"},
    ]


def test_leaves_the_status_for_an_empty_list(run: SimpleNamespace) -> None:
    # As a to-do list is before the agent writes one.
    async def scenario() -> None:
        middleware = _middleware(lambda _context: [])
        await middleware.abefore_agent(_state("hello"), _runtime())  # type: ignore[arg-type]
        await _settle()

    asyncio.run(scenario())
    assert run.client.events == []


def test_posts_only_a_changed_status_with_the_newest_state(run: SimpleNamespace) -> None:
    asked: list[object] = []

    def chooser(context: dict[str, Any]) -> str:
        asked.append(context["state"]["messages"][0])
        return "is on it" if context["state"]["messages"][0] != "c" else "saw c"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle()
        # Both arrive within the second; only the newest is asked about.
        await middleware.abefore_model(_state("b"), _runtime())  # type: ignore[arg-type]
        await middleware.aafter_model(_state("c"), _runtime())  # type: ignore[arg-type]
        await _settle(0.1)
        await middleware.abefore_model(_state("d"), _runtime())  # type: ignore[arg-type]
        await _settle(0.1)

    asyncio.run(scenario())
    assert asked == ["a", "c", "d"]
    texts = [event["message"]["text"] for _, event in run.client.events]
    # "d" says "is on it" again, which isn't the shown "saw c": it's posted.
    assert texts == ["is on it", "saw c", "is on it"]


def test_asks_again_while_a_call_runs(run: SimpleNamespace) -> None:
    count = 0

    def chooser(context: object) -> str:
        nonlocal count
        count += 1
        return f"tick {count}"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        request = SimpleNamespace(state=_state("a"), runtime=_runtime())

        async def handler(_request: object) -> str:
            await asyncio.sleep(0.18)
            return "done"

        assert await middleware.awrap_tool_call(request, handler) == "done"  # type: ignore[arg-type]
        calls = count
        await _settle(0.2)
        # The heartbeat stopped with the call.
        assert count == calls

    asyncio.run(scenario())
    assert len(run.client.events) >= 2


def _raises(context: object) -> str:
    raise RuntimeError("boom")


async def _hangs(context: object) -> str:
    await asyncio.sleep(10)
    return "late"


@pytest.mark.parametrize(
    "chooser",
    [lambda context: None, _raises, lambda context: 42, _hangs],
    ids=["none", "raises", "not-a-status", "too-slow"],
)
def test_leaves_the_status_when_the_chooser_does_not_answer(
    run: SimpleNamespace, chooser: Callable[[object], object]
) -> None:
    async def scenario() -> None:
        middleware = _middleware(chooser)
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle(0.15)

    asyncio.run(scenario())
    assert run.client.events == []


def test_a_failed_post_never_fails_the_run(run: SimpleNamespace) -> None:
    run.client = _Client(fail=True)

    async def scenario() -> object:
        middleware = _middleware(lambda context: "is on it")
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle()

        async def handler(_request: object) -> str:
            return "answer"

        request = SimpleNamespace(state=_state("a"), runtime=_runtime())
        return await middleware.awrap_model_call(request, handler)  # type: ignore[arg-type]

    assert asyncio.run(scenario()) == "answer"


def test_posts_a_status_again_after_its_post_failed(run: SimpleNamespace) -> None:
    run.client = _Client(fail=True)

    async def scenario() -> None:
        middleware = _middleware(lambda context: "is on it")
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle()
        run.client._fail = False
        await _settle(0.06)
        await middleware.abefore_model(_state("b"), _runtime())  # type: ignore[arg-type]
        await _settle()

    asyncio.run(scenario())
    assert len(run.client.events) == 1


def test_the_end_of_the_run_waits_for_an_update_on_its_way(run: SimpleNamespace) -> None:
    run.client = _Client(delay=0.1)

    async def scenario() -> None:
        middleware = _middleware(lambda context: "is on it")
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle()
        await middleware.aafter_agent(_state("a"), _runtime())  # type: ignore[arg-type]

    asyncio.run(scenario())
    assert len(run.client.events) == 1


def test_drops_an_update_still_waiting_when_the_run_ends(run: SimpleNamespace) -> None:
    count = 0

    def chooser(context: object) -> str:
        nonlocal count
        count += 1
        return f"update {count}"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle()
        await middleware.aafter_model(_state("b"), _runtime())  # type: ignore[arg-type]
        await middleware.aafter_agent(_state("b"), _runtime())  # type: ignore[arg-type]
        await _settle(0.1)

    asyncio.run(scenario())
    assert count == 1
    assert len(run.client.events) == 1


def test_shows_nothing_more_once_a_call_fails(run: SimpleNamespace) -> None:
    count = 0

    def chooser(context: object) -> str:
        nonlocal count
        count += 1
        return f"update {count}"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        request = SimpleNamespace(state=_state("a"), runtime=_runtime())
        finish = asyncio.Event()

        async def runs(_request: object) -> str:
            await finish.wait()
            return "done"

        async def fails(_request: object) -> str:
            raise RuntimeError("interrupted")

        # Two tool calls run side by side; one fails, and with it the run.
        running = asyncio.create_task(middleware.awrap_tool_call(request, runs))  # type: ignore[arg-type]
        await _settle()
        with pytest.raises(RuntimeError, match="interrupted"):
            await middleware.awrap_tool_call(request, fails)  # type: ignore[arg-type]
        await _settle(0.2)
        finish.set()
        await running

    asyncio.run(scenario())
    assert run.client.events == []


def test_drops_what_a_call_asked_about_once_it_returns(run: SimpleNamespace) -> None:
    async def chooser(context: object) -> str:
        await asyncio.sleep(0.04)
        return "is on it"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        request = SimpleNamespace(state=_state("a"), runtime=_runtime())

        async def handler(_request: object) -> str:
            # The heartbeat asks at 0.05 s; the call returns before the answer.
            await asyncio.sleep(0.07)
            return "answer"

        await middleware.awrap_model_call(request, handler)  # type: ignore[arg-type]
        await _settle(0.1)
        # A later hook, such as an approval interrupt, may stop the run here.
        assert run.client.events == []
        await middleware.aafter_model(_state("b"), _runtime())  # type: ignore[arg-type]
        await _settle(0.1)

    asyncio.run(scenario())
    assert len(run.client.events) == 1


def test_leaves_the_status_to_the_runs_own_agent_not_a_fork(run: SimpleNamespace) -> None:
    asked: list[object] = []

    def chooser(context: dict[str, Any]) -> str:
        asked.append(context["state"]["messages"][0])
        return "is on it"

    async def scenario() -> None:
        middleware = _middleware(chooser)
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]
        await _settle()
        # A fork in the run's `task` tool shares its config, one graph down.
        run.config = _config(namespace="tools:1|model:2")
        await middleware.abefore_model(_state("fork"), _runtime())  # type: ignore[arg-type]
        await middleware.aafter_agent(_state("fork"), _runtime())  # type: ignore[arg-type]
        run.config = _config()
        await _settle(0.1)
        await middleware.abefore_model(_state("b"), _runtime())  # type: ignore[arg-type]
        await _settle()

    asyncio.run(scenario())
    assert asked == ["a", "b"]
    # The run's tracker survived the fork, so "is on it" isn't posted again.
    assert len(run.client.events) == 1


def test_ignores_a_run_whose_binding_does_not_check_out(run: SimpleNamespace) -> None:
    run.config = _config(provider="github")
    asked: list[object] = []

    async def scenario() -> None:
        middleware = _middleware(asked.append)
        await middleware.abefore_agent(_state("a"), _runtime())  # type: ignore[arg-type]

        async def handler(_request: object) -> str:
            return "done"

        request = SimpleNamespace(state=_state("a"), runtime=_runtime())
        assert await middleware.awrap_tool_call(request, handler) == "done"  # type: ignore[arg-type]
        await _settle(0.1)

    asyncio.run(scenario())
    assert asked == []
    assert run.client.events == []


def test_a_sync_run_passes_through(run: SimpleNamespace) -> None:
    middleware = _middleware(lambda context: "is on it")
    request = SimpleNamespace(state=_state("a"), runtime=_runtime())
    assert middleware.before_agent(_state("a"), _runtime()) is None  # type: ignore[arg-type]
    assert middleware.wrap_tool_call(request, lambda _request: "done") == "done"  # type: ignore[arg-type]
    assert run.client.events == []


def test_the_runtime_installs_the_middleware_only_for_a_chooser() -> None:
    from managed_deepagents.runtime import _prepend_slack_files_middleware  # noqa: PLC0415

    def names(channel: object) -> list[str]:
        merged: dict[str, object] = {}
        _prepend_slack_files_middleware(merged, [channel])  # type: ignore[list-item]
        return [middleware.name for middleware in merged["middleware"]]  # type: ignore[attr-defined]

    chooser = channels.slack(status=lambda context: "is on it")
    chooser.name = "team"
    assert names(chooser) == ["mda-slack-file-transfer", "mda-slack-status"]
    # A fixed status goes on `run.started`; the run has nothing to ask.
    fixed = channels.slack(status="is on it")
    fixed.name = "team"
    assert names(fixed) == ["mda-slack-file-transfer"]
