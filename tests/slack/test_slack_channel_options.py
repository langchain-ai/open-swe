"""The channel directory behind the workspace Slack picker."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError

from agent.slack import channel_options
from agent.utils import ttl_cache


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


def _install(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> list[float]:
    @asynccontextmanager
    async def fake_slack_client(**_kwargs: Any):
        yield client

    monkeypatch.setattr(channel_options, "slack_client", fake_slack_client)
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        # Record the wait but still yield once: the module patches the asyncio
        # module itself, and tests rely on a zero sleep to let deferred
        # callbacks run.
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(channel_options.asyncio, "sleep", fake_sleep)
    return slept


_MEMBER_PAGE = {
    "channels": [
        {"id": "G1", "name": "oss-maintainers", "is_private": True, "num_members": 41},
    ],
    "response_metadata": {"next_cursor": ""},
}


async def test_merges_the_bots_channels_with_the_public_directory_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            {
                "channels": [
                    {"id": "C3", "name": "community", "is_member": False, "num_members": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        ],
    )
    _install(monkeypatch, client)

    directory = await channel_options.list_slack_channels()

    assert directory.partial is False
    assert [option.id for option in directory.channels] == ["C3", "C2", "G1"]
    # users.conversations does not say is_member; membership is what it lists.
    assert directory.channels[2].is_member is True and directory.channels[2].is_private is True
    assert directory.channels[1].num_members == 2310
    # A boolean where Slack promised a count is not a count.
    assert directory.channels[0].num_members is None and directory.channels[0].is_member is False
    assert client.calls["conversations_list"][0]["limit"] == 1000
    assert client.calls["conversations_list"][0]["exclude_archived"] is True
    assert client.calls["conversations_list"][1]["cursor"] == "page-2"

    # A second browse within the TTL is served from cache.
    assert await channel_options.list_slack_channels() == directory
    assert len(client.calls["conversations_list"]) == 2
    assert len(client.calls["users_conversations"]) == 1


async def test_a_brief_rate_limit_is_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        [_MEMBER_PAGE],
        [
            _rate_limited("2"),
            {
                "channels": [{"id": "C2", "name": "oss-help", "is_member": False}],
                "response_metadata": {"next_cursor": ""},
            },
        ],
    )
    slept = _install(monkeypatch, client)

    directory = await channel_options.list_slack_channels()

    assert slept == [2.0]
    assert directory.partial is False
    assert [option.id for option in directory.channels] == ["C2", "G1"]


async def test_a_long_rate_limit_leaves_the_directory_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_MEMBER_PAGE], [_rate_limited("30")])
    slept = _install(monkeypatch, client)

    directory = await channel_options.list_slack_channels()

    assert slept == []
    assert directory.partial is True
    assert [option.id for option in directory.channels] == ["G1"]
    # Served from cache for now, so the next browse does not hammer Slack.
    assert await channel_options.list_slack_channels() == directory
    assert len(client.calls["conversations_list"]) == 1


async def test_a_partial_directory_is_retried_on_schedule_however_often_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(ttl_cache, "_now", lambda: clock["now"])
    client = _FakeClient(
        [_MEMBER_PAGE, _MEMBER_PAGE],
        [
            _rate_limited("30"),
            {
                "channels": [{"id": "C2", "name": "oss-help", "is_member": False}],
                "response_metadata": {"next_cursor": ""},
            },
        ],
    )
    _install(monkeypatch, client)

    assert (await channel_options.list_slack_channels()).partial is True
    await asyncio.sleep(0)  # the shorter expiry lands on the next loop iteration

    clock["now"] += 30
    assert (await channel_options.list_slack_channels()).partial is True
    assert len(client.calls["conversations_list"]) == 1

    # Past the short expiry the stale directory is served once more while a
    # refresh runs in the background; the next read has the full list.
    clock["now"] += 40
    assert (await channel_options.list_slack_channels()).partial is True
    await asyncio.gather(*ttl_cache._REFRESH_TASKS.values())
    assert len(client.calls["conversations_list"]) == 2
    directory = await channel_options.list_slack_channels()

    assert [option.id for option in directory.channels] == ["C2", "G1"]
    assert directory.partial is False


async def test_rate_limiting_the_bots_own_channels_asks_the_admin_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeClient([_rate_limited("30")], []))

    with pytest.raises(HTTPException) as excinfo:
        await channel_options.list_slack_channels()

    assert excinfo.value.status_code == 429


async def test_a_repeating_cursor_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {"channels": [], "response_metadata": {"next_cursor": "loop"}}
    _install(monkeypatch, _FakeClient([page, page, page], []))

    with pytest.raises(HTTPException) as excinfo:
        await channel_options.list_slack_channels()

    assert excinfo.value.status_code == 502
