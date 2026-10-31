"""Resolving the reaction emoji for one inbound message."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest

from managed_deepagents._channels.reactions import (
    DEFAULT_REACTION_EMOJI,
    ReactionContext,
    is_emoji_name,
    resolve_reaction_emoji,
)


@pytest.mark.parametrize("name", ["eyes", "panda_face", "star-struck", "+1", "-1"])
def test_accepts_slack_short_names(name: str) -> None:
    assert is_emoji_name(name)


@pytest.mark.parametrize("value", [":eyes:", "Eyes", "two words", "", "a" * 61, "eyes\n", None, 42])
def test_rejects_anything_slack_could_not_resolve(value: object) -> None:
    assert not is_emoji_name(value)


def test_uses_a_short_name_as_given() -> None:
    assert asyncio.run(resolve_reaction_emoji("wave", {"text": "hi"})) == "wave"


def test_uses_the_default_for_true() -> None:
    assert asyncio.run(resolve_reaction_emoji(True, {"text": "hi"})) == DEFAULT_REACTION_EMOJI


def test_falls_back_when_a_configured_string_is_not_an_emoji_name() -> None:
    assert asyncio.run(resolve_reaction_emoji(":wave:", {"text": "hi"})) == DEFAULT_REACTION_EMOJI


def test_calls_a_chooser_with_the_message_text() -> None:
    seen: list[object] = []

    def chooser(context: object) -> str:
        seen.append(context)
        return "bug"

    assert asyncio.run(resolve_reaction_emoji(chooser, {"text": "the build is broken"})) == "bug"
    assert seen == [{"text": "the build is broken"}]


def test_awaits_an_async_chooser() -> None:
    async def chooser(context: object) -> str:
        await asyncio.sleep(0)
        return "rocket"

    assert asyncio.run(resolve_reaction_emoji(chooser, {"text": "ship it"})) == "rocket"


def _raises(context: object) -> str:
    raise RuntimeError("boom")


async def _rejects(context: object) -> str:
    raise RuntimeError("boom")


@pytest.mark.parametrize(
    "chooser",
    [
        _raises,
        _rejects,
        lambda context: ":bug:",
        lambda context: 7,
        "not-callable-and-not-a-name:",
    ],
    ids=["raises", "rejects", "not-a-name", "not-a-string", "unusable-string"],
)
def test_falls_back_on_a_chooser_that_does_not_answer(chooser: object) -> None:
    assert asyncio.run(resolve_reaction_emoji(chooser, {"text": "hello"})) == DEFAULT_REACTION_EMOJI


def test_a_blocking_sync_chooser_does_not_hold_the_event_loop() -> None:
    """The documented sync shape invites a blocking HTTP call, and this runs on
    the loop that is posting the reply."""

    def blocking(context: object) -> str:
        time.sleep(0.3)
        return "bug"

    async def main() -> tuple[str, int]:
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker = asyncio.create_task(tick())
        chosen = await resolve_reaction_emoji(blocking, {"text": "hello"})
        ticker.cancel()
        return chosen, ticks

    chosen, ticks = asyncio.run(main())
    assert chosen == "bug"
    assert ticks > 0


def test_gives_up_on_a_slow_sync_chooser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("managed_deepagents._channels.reactions._CHOOSER_TIMEOUT_S", 0.05)

    # Short: asyncio.run waits for the worker thread even after the timeout.
    def blocking(context: object) -> str:
        time.sleep(0.5)
        return "bug"

    assert (
        asyncio.run(resolve_reaction_emoji(blocking, {"text": "hello"})) == DEFAULT_REACTION_EMOJI
    )


def test_gives_up_on_a_chooser_that_never_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("managed_deepagents._channels.reactions._CHOOSER_TIMEOUT_S", 0.05)

    async def chooser(context: object) -> str:
        await asyncio.sleep(10)
        return "rocket"

    assert asyncio.run(resolve_reaction_emoji(chooser, {"text": "hello"})) == DEFAULT_REACTION_EMOJI


async def _skip_reaction(context: ReactionContext) -> None:
    return None


@pytest.mark.parametrize("chooser", [lambda context: None, _skip_reaction])
def test_chooser_can_skip_reaction(
    chooser: Callable[[ReactionContext], Awaitable[None] | None],
) -> None:
    assert asyncio.run(resolve_reaction_emoji(chooser, {"text": "hi"})) is None
