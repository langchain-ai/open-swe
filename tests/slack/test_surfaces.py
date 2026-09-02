from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.source_context import SlackThreadRef, SourceContext
from agent.surfaces import (
    WEB_SURFACE,
    SlackChannelSurface,
    SlackThreadSurface,
    resolve_surface,
    slack_surface,
    surface_from_metadata,
)
from agent.surfaces import slack as surfaces_slack


def _channel_ref(**overrides: Any) -> SlackThreadRef:
    return SlackThreadRef(
        channel_id="C1",
        thread_ts="0",
        surface="slack_channel",
        **overrides,
    )


def test_explicit_surface_selects_the_channel() -> None:
    surface = slack_surface(_channel_ref())
    assert isinstance(surface, SlackChannelSurface)
    assert surface.kind == "slack_channel"


def test_channel_session_timestamp_is_the_fallback_signal() -> None:
    """Locations written before `surface` existed only carry the sentinel ts."""
    surface = slack_surface(SlackThreadRef(channel_id="C1", thread_ts="0"))
    assert isinstance(surface, SlackChannelSurface)


def test_unknown_surface_falls_back_instead_of_discarding_the_location() -> None:
    ref = SlackThreadRef.parse(
        {"channel_id": "C1", "thread_ts": "1717171717.123456", "surface": "hologram"}
    )
    assert ref is not None
    assert ref.surface is None
    assert isinstance(slack_surface(ref), SlackThreadSurface)


def test_thread_surface_replies_in_its_thread() -> None:
    surface = slack_surface(SlackThreadRef(channel_id="C1", thread_ts="1717171717.123456"))
    assert isinstance(surface, SlackThreadSurface)
    assert surface.reply_target() == "1717171717.123456"
    assert surface.prompt_section() == ""
    assert surface.web_link_thread_id("thread-1") == "thread-1"


def test_channel_surface_replies_at_channel_level_by_default() -> None:
    assert slack_surface(_channel_ref()).reply_target() == "0"


def test_channel_surface_replies_in_a_thread_the_user_started() -> None:
    surface = slack_surface(_channel_ref(reply_thread_ts="1717171717.123456"))
    assert surface.reply_target() == "1717171717.123456"


def test_channel_surface_omits_the_per_thread_web_link() -> None:
    assert slack_surface(_channel_ref()).web_link_thread_id("thread-1") is None


def test_channel_surface_describes_itself_to_the_agent() -> None:
    assert "one session" in slack_surface(_channel_ref()).prompt_section()


def test_locations_without_a_channel_are_not_slack_surfaces() -> None:
    assert slack_surface(None) is WEB_SURFACE
    assert slack_surface(SlackThreadRef()) is WEB_SURFACE
    assert resolve_surface(None) is WEB_SURFACE
    assert resolve_surface(SourceContext()) is WEB_SURFACE
    assert surface_from_metadata({"source": "dashboard"}) is WEB_SURFACE


def test_unparseable_location_yields_no_ref() -> None:
    assert SlackThreadRef.parse("not a mapping") is None
    assert SlackThreadRef.parse(None) is None


def test_surface_comes_from_thread_metadata() -> None:
    metadata = {
        "source_context": SourceContext(slack_thread=_channel_ref()).dump(),
    }
    assert isinstance(surface_from_metadata(metadata), SlackChannelSurface)


def test_web_surface_has_nothing_to_report_or_publish() -> None:
    assert WEB_SURFACE.reports_activity is False
    assert WEB_SURFACE.has_chrome is False
    assert WEB_SURFACE.reply_target() is None


async def test_web_surface_chrome_calls_are_inert() -> None:
    await WEB_SURFACE.begin_turn()
    await WEB_SURFACE.end_turn()
    await WEB_SURFACE.start_session(repo={"owner": "o", "name": "n"}, thread_id="thread-1")
    await WEB_SURFACE.set_title("Title")
    await WEB_SURFACE.publish_diff("diff", base_branch="main", head_branch="topic")


async def test_thread_surface_has_no_session_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    status = AsyncMock()
    monkeypatch.setattr(surfaces_slack, "set_session_status", status)
    monkeypatch.setattr(surfaces_slack, "rename_session", AsyncMock())

    surface = SlackThreadSurface("C1", "1717171717.123456")
    assert surface.reports_activity is False
    await surface.begin_turn()
    await surface.end_turn()
    await surface.set_title("Title")
    status.assert_not_awaited()


async def test_channel_surface_reports_turn_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    status = AsyncMock()
    monkeypatch.setattr(surfaces_slack, "set_session_status", status)

    surface = SlackChannelSurface("C1")
    await surface.begin_turn()
    await surface.end_turn()
    assert [call.args for call in status.await_args_list] == [
        ("C1", "processing"),
        ("C1", "active"),
    ]


async def test_channel_surface_seeds_context_and_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    context_bar = AsyncMock(return_value=(True, None))
    commands = AsyncMock(return_value=({}, None))
    monkeypatch.setattr(surfaces_slack, "set_context_bar", context_bar)
    monkeypatch.setattr(surfaces_slack, "set_commands", commands)

    await SlackChannelSurface("C1").start_session(
        repo={"owner": "acme", "name": "billing"}, thread_id="thread-1"
    )
    channel_id, items = context_bar.await_args_list[0].args
    assert channel_id == "C1"
    assert {"key": "repo", "label": "acme/billing"}.items() <= items[0].items()
    assert commands.await_args_list[0].args[0] == "C1"


async def test_channel_surface_skips_an_empty_context_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty bar would clear the items already on the channel."""
    context_bar = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(surfaces_slack, "set_context_bar", context_bar)

    await SlackChannelSurface("C1").sync_context(repo=None, thread_id="")
    context_bar.assert_not_awaited()


async def test_channel_surface_publishes_a_diff_view(monkeypatch: pytest.MonkeyPatch) -> None:
    view = AsyncMock(return_value=({}, None))
    monkeypatch.setattr(surfaces_slack, "set_view", view)

    await SlackChannelSurface("C1").publish_diff(
        "diff --git a/x b/x", base_branch="main", head_branch="topic"
    )
    call = view.await_args_list[0]
    assert call.args == ("C1", "diff")
    assert call.kwargs == {
        "content": "diff --git a/x b/x",
        "base_branch": "main",
        "head_branch": "topic",
    }
