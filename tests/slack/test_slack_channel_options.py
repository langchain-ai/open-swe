"""The channel directory behind the workspace Slack picker."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from slack_sdk.errors import SlackApiError

from openswe.jobs import queue
from openswe.slack import channel_options, channels, dashboard_routes
from openswe.slack.channels import SlackChannel


def _rate_limited(retry_after: str) -> SlackApiError:
    response = SimpleNamespace(
        status_code=429,
        data={"ok": False, "error": "ratelimited"},
        headers={"Retry-After": retry_after},
    )
    return SlackApiError("ratelimited", response)


class _FakeClient:
    """Pages per method; an exception in a page list is raised in its turn."""

    token = "xoxb-test"

    def __init__(self, member_pages: list[Any], public_pages: list[Any]) -> None:
        self.pages = {"users_conversations": member_pages, "conversations_list": public_pages}
        self.calls: dict[str, list[dict[str, Any]]] = {
            "users_conversations": [],
            "conversations_list": [],
        }
        self.info: dict[str, dict[str, Any]] = {}

    async def _page(self, method: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls[method].append(kwargs)
        page = self.pages[method][len(self.calls[method]) - 1]
        if isinstance(page, Exception):
            raise page
        return page

    async def users_conversations(self, **kwargs: Any) -> dict[str, Any]:
        return await self._page("users_conversations", kwargs)

    async def conversations_list(self, **kwargs: Any) -> dict[str, Any]:
        return await self._page("conversations_list", kwargs)

    async def conversations_info(self, *, channel: str) -> dict[str, Any]:
        return {"channel": self.info[channel]}


def _install(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> list[float]:
    @asynccontextmanager
    async def fake_slack_client(**_kwargs: Any):
        yield client

    monkeypatch.setattr(channels.SlackClient, "bot", fake_slack_client)
    monkeypatch.setattr(channels, "SLACK_BOT_TOKEN", "xoxb-test")
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(channels.asyncio, "sleep", fake_sleep)
    return slept


_MEMBER_PAGE = {
    "channels": [
        {"id": "G1", "name": "oss-maintainers", "is_private": True, "num_members": 41},
    ],
    "response_metadata": {"next_cursor": ""},
}


@pytest.mark.usefixtures("registry_db")
async def test_a_walk_lists_what_slack_shows_and_unlists_what_it_no_longer_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await SlackChannel.save_all({"id": "C0GONE", "name": "deleted-since", "is_member": True})
    client = _FakeClient(
        [_MEMBER_PAGE],
        [
            {
                "channels": [
                    {"id": "C2", "name": "oss-help", "is_member": True, "num_members": 2310},
                    {"id": "G1", "name": "oss-maintainers", "is_private": True, "is_member": True},
                    {"id": "C9", "name": "", "is_member": True},
                    "junk",
                ],
                "response_metadata": {"next_cursor": "page-2"},
            },
            _rate_limited("30"),
            {
                "channels": [
                    {"id": "C3", "name": "Community", "is_member": False, "num_members": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        ],
    )
    slept = _install(monkeypatch, client)

    await channel_options.sync_directory()

    directory = await channel_options.list_slack_channels()
    assert [option.id for option in directory.channels] == ["C3", "C2", "G1"]
    assert slept == [30.0]
    assert client.calls["conversations_list"][2]["cursor"] == "page-2"
    # users.conversations does not say is_member; membership is what it lists.
    assert directory.channels[2].is_member is True and directory.channels[2].is_private is True
    assert directory.channels[1].num_members == 2310
    # Slack's own spelling is shown; a boolean where Slack promised a count is not a count.
    assert directory.channels[0].name == "Community"
    assert directory.channels[0].num_members is None and directory.channels[0].is_member is False
    # The gone channel keeps its row for the records that reference it.
    assert (await SlackChannel._row("C0GONE")) is not None


@pytest.mark.usefixtures("registry_db")
async def test_a_walk_cut_short_unlists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    await SlackChannel.save_all({"id": "C1", "name": "kept"})
    _install(
        monkeypatch,
        _FakeClient(
            [],
            [
                SlackApiError(
                    "boom",
                    SimpleNamespace(
                        status_code=200, data={"ok": False, "error": "internal_error"}, headers={}
                    ),
                )
            ],
        ),
    )

    with pytest.raises(SlackApiError):
        await channel_options.sync_directory()

    assert [option.id for option in (await channel_options.list_slack_channels()).channels] == [
        "C1"
    ]


@pytest.mark.usefixtures("registry_db")
async def test_channel_events_keep_the_directory_current(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([], [])
    _install(monkeypatch, client)
    await SlackChannel.save_all({"id": "C1", "name": "old-name"}, {"id": "C2", "name": "doomed"})

    client.info["C1"] = {"id": "C1", "name": "new-name", "is_member": True}
    await SlackChannel.observe("channel_rename", "C1")
    await SlackChannel.observe("channel_archive", "C2")

    directory = await channel_options.list_slack_channels()
    assert [(option.id, option.name) for option in directory.channels] == [("C1", "new-name")]
    assert (await SlackChannel.resolve("#new-name")) is not None


@pytest.mark.usefixtures("registry_db")
async def test_refreshing_brings_the_next_walk_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    await channel_options.ensure_directory_sync()
    assert (await channel_options.list_slack_channels()).refreshing is True

    [walk] = await queue.claim_due(1)
    await walk.finish()
    assert (await channel_options.list_slack_channels()).refreshing is False

    await channel_options.refresh_directory(by=None)
    assert (await channel_options.list_slack_channels()).refreshing is True
    assert [job.id for job in await queue.claim_due(1)] == [walk.id]


@pytest.mark.parametrize("admin", [True, False])
async def test_private_channels_are_listed_for_admins_only(
    monkeypatch: pytest.MonkeyPatch, admin: bool
) -> None:
    def option(channel_id: str, *, private: bool) -> channel_options.SlackChannelOption:
        return channel_options.SlackChannelOption(
            id=channel_id,
            name=channel_id.lower(),
            is_private=private,
            is_member=True,
            is_ext_shared=False,
        )

    directory = channel_options.SlackChannelDirectory(
        channels=[option("C0PUBLIC", private=False), option("G0PRIVATE", private=True)]
    )
    monkeypatch.setattr(dashboard_routes, "list_slack_channels", AsyncMock(return_value=directory))
    monkeypatch.setattr(dashboard_routes, "session_is_admin", lambda _session: admin)

    listed = await dashboard_routes.api_list_slack_channels({"sub": "someone"})

    assert [channel.id for channel in listed.channels] == (
        ["C0PUBLIC", "G0PRIVATE"] if admin else ["C0PUBLIC"]
    )
