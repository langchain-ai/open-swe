from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.utils import source_channel
from agent.utils.source_channel import (
    SourceContext,
    in_graph_github_token,
    notify_source_channel,
    source_context_from_configurable,
    source_context_from_thread_metadata,
    source_context_from_watch,
)


async def _no_token() -> str | None:
    return None


async def _app_token() -> str | None:
    return "app-token"


def _context(
    *,
    source: str | None = None,
    slack_thread: dict[str, str] | None = None,
    linear_issue_id: str | None = None,
    github_item: dict[str, Any] | None = None,
) -> SourceContext:
    return {
        "source": source,
        "slack_thread": slack_thread,  # type: ignore[typeddict-item]
        "linear_issue_id": linear_issue_id,
        "github_item": github_item,  # type: ignore[typeddict-item]
    }


class _Channels:
    def __init__(self) -> None:
        self.slack = AsyncMock(return_value=True)
        self.linear = AsyncMock(return_value=True)
        self.github = AsyncMock(return_value=True)


@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch) -> _Channels:
    posters = _Channels()
    monkeypatch.setattr(source_channel, "post_slack_thread_reply", posters.slack)
    monkeypatch.setattr(source_channel, "comment_on_linear_issue", posters.linear)
    monkeypatch.setattr(source_channel, "post_github_comment", posters.github)
    return posters


_ALL_CHANNELS = _context(
    source="slack",
    slack_thread={"channel_id": "C1", "thread_ts": "1.2"},
    linear_issue_id="iss_1",
    github_item={"repo": {"owner": "acme", "name": "widgets"}, "number": 7},
)


async def test_slack_wins_over_linear_and_github(channels: _Channels) -> None:
    assert await notify_source_channel(_ALL_CHANNELS, "hi", github_token=_app_token) is True

    channels.slack.assert_awaited_once_with("C1", "1.2", "hi")
    channels.linear.assert_not_called()
    channels.github.assert_not_called()


async def test_linear_is_used_when_there_is_no_slack_thread(channels: _Channels) -> None:
    context = _context(
        source="linear",
        linear_issue_id="iss_1",
        github_item={"repo": {"owner": "acme", "name": "widgets"}, "number": 7},
    )

    assert await notify_source_channel(context, "hi", github_token=_app_token) is True

    channels.slack.assert_not_called()
    channels.linear.assert_awaited_once_with("iss_1", "hi")
    channels.github.assert_not_called()


async def test_github_is_the_last_rung(channels: _Channels) -> None:
    context = _context(
        source="github", github_item={"repo": {"owner": "acme", "name": "widgets"}, "number": 7}
    )

    assert await notify_source_channel(context, "hi", github_token=_app_token) is True

    channels.slack.assert_not_called()
    channels.linear.assert_not_called()
    channels.github.assert_awaited_once_with(
        {"owner": "acme", "name": "widgets"}, 7, "hi", token="app-token"
    )


async def test_github_rung_is_skipped_without_a_token(channels: _Channels) -> None:
    context = _context(
        source="github", github_item={"repo": {"owner": "acme", "name": "widgets"}, "number": 7}
    )

    assert await notify_source_channel(context, "hi", github_token=_no_token) is False

    channels.github.assert_not_called()


async def test_no_configured_channel_reports_failure(channels: _Channels) -> None:
    assert (
        await notify_source_channel(_context(source="schedule"), "hi", github_token=_app_token)
        is False
    )

    channels.slack.assert_not_called()
    channels.linear.assert_not_called()
    channels.github.assert_not_called()


async def test_slack_text_overrides_the_body_on_slack_only(channels: _Channels) -> None:
    await notify_source_channel(
        _ALL_CHANNELS, "plain", slack_text="<https://ui/t1|Open SWE Web>", github_token=_app_token
    )

    channels.slack.assert_awaited_once_with("C1", "1.2", "<https://ui/t1|Open SWE Web>")


async def test_agent_thread_id_decorates_the_slack_reply(channels: _Channels) -> None:
    await notify_source_channel(_ALL_CHANNELS, "hi", github_token=_app_token, agent_thread_id="t1")

    channels.slack.assert_awaited_once_with("C1", "1.2", "hi", agent_thread_id="t1")


async def test_live_thread_lookup_overrides_a_stale_slack_location(
    channels: _Channels, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    lookup = AsyncMock(return_value={"channel_id": "C-new", "thread_ts": "9.9"})
    monkeypatch.setattr(source_channel, "get_active_slack_thread", lookup)

    await notify_source_channel(
        _ALL_CHANNELS,
        "hi",
        github_token=_app_token,
        agent_thread_id="t1",
        langgraph_client_factory=lambda: client,
    )

    lookup.assert_awaited_once_with(client, "t1", {"channel_id": "C1", "thread_ts": "1.2"})
    channels.slack.assert_awaited_once_with("C-new", "9.9", "hi", agent_thread_id="t1")


async def test_without_a_client_factory_the_thread_is_never_read(
    channels: _Channels, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(source_channel, "get_active_slack_thread", lookup)

    await notify_source_channel(_ALL_CHANNELS, "hi", github_token=_app_token)

    lookup.assert_not_awaited()
    channels.slack.assert_awaited_once_with("C1", "1.2", "hi")


async def test_a_failing_channel_never_raises(channels: _Channels) -> None:
    channels.slack.side_effect = RuntimeError("slack is down")

    assert await notify_source_channel(_ALL_CHANNELS, "hi", github_token=_app_token) is False


async def test_in_graph_token_prefers_the_triggering_user(monkeypatch: pytest.MonkeyPatch) -> None:
    app_token = AsyncMock(return_value="app-token")
    monkeypatch.setattr(source_channel, "get_github_token", lambda _config: "user-token")
    monkeypatch.setattr(source_channel, "get_github_app_installation_token", app_token)

    assert await in_graph_github_token({"configurable": {}})() == "user-token"
    app_token.assert_not_awaited()


async def test_in_graph_token_falls_back_to_the_app_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_channel, "get_github_token", lambda _config: None)
    monkeypatch.setattr(
        source_channel, "get_github_app_installation_token", AsyncMock(return_value="app-token")
    )

    assert await in_graph_github_token({"configurable": {}})() == "app-token"


def test_thread_metadata_joins_the_top_level_repo_to_the_context_number() -> None:
    context = source_context_from_thread_metadata(
        {
            "source": "github",
            "repo": {"owner": "acme", "name": "widgets"},
            "source_context": {
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
                "linear_issue": {"id": "iss_1"},
                "pr_number": 7,
            },
        }
    )

    assert context == {
        "source": "github",
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        "linear_issue_id": "iss_1",
        "github_item": {"repo": {"owner": "acme", "name": "widgets"}, "number": 7},
    }


def test_thread_metadata_falls_back_to_the_github_issue_number() -> None:
    context = source_context_from_thread_metadata(
        {
            "source": "github_issue",
            "repo": {"owner": "acme", "name": "widgets"},
            "source_context": {"github_issue": {"number": 12}},
        }
    )

    assert context["github_item"] == {"repo": {"owner": "acme", "name": "widgets"}, "number": 12}


def test_thread_metadata_without_a_source_context_has_no_targets() -> None:
    assert source_context_from_thread_metadata({"source": "schedule"}) == {
        "source": "schedule",
        "slack_thread": None,
        "linear_issue_id": None,
        "github_item": None,
    }


def test_configurable_prefers_the_repo_named_by_the_triggering_item() -> None:
    context = source_context_from_configurable(
        {
            "source": "github",
            "repo": {"owner": "acme", "name": "widgets"},
            "github_pr_or_issue": {"number": "31", "repo": {"owner": "fork", "name": "widgets"}},
            "pr_number": 7,
        }
    )

    assert context["github_item"] == {"repo": {"owner": "fork", "name": "widgets"}, "number": 31}


def test_configurable_falls_back_through_github_issue_to_pr_number() -> None:
    repo = {"owner": "acme", "name": "widgets"}

    from_issue = source_context_from_configurable({"repo": repo, "github_issue": {"number": 12}})
    from_pr = source_context_from_configurable({"repo": repo, "pr_number": 7})

    assert from_issue["github_item"] == {"repo": repo, "number": 12}
    assert from_pr["github_item"] == {"repo": repo, "number": 7}


def test_configurable_reads_slack_and_linear_targets() -> None:
    context = source_context_from_configurable(
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
            "linear_issue": {"id": "iss_1"},
        }
    )

    assert context == {
        "source": "slack",
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
        "linear_issue_id": "iss_1",
        "github_item": None,
    }


def test_a_half_written_slack_thread_is_not_a_target() -> None:
    context = source_context_from_configurable({"slack_thread": {"channel_id": "C1"}})

    assert context["slack_thread"] is None


def test_watch_always_knows_its_own_repo_and_pull_request() -> None:
    context = source_context_from_watch(
        {
            "owner": "acme",
            "repo": "widgets",
            "run_config": {"source": "dashboard", "pr_number": 7},
            "source_context": {},
        }
    )

    assert context == {
        "source": "dashboard",
        "slack_thread": None,
        "linear_issue_id": None,
        "github_item": {"repo": {"owner": "acme", "name": "widgets"}, "number": 7},
    }


def test_watch_prefers_the_recorded_github_issue_over_the_run_config() -> None:
    context = source_context_from_watch(
        {
            "owner": "acme",
            "repo": "widgets",
            "run_config": {"pr_number": 7},
            "source_context": {
                "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
                "github_issue": {"number": 12},
            },
        }
    )

    assert context["github_item"] == {"repo": {"owner": "acme", "name": "widgets"}, "number": 12}
    assert context["slack_thread"] == {"channel_id": "C1", "thread_ts": "1.2"}
