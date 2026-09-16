"""The channel directory behind the workspace Slack picker."""

from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import HTTPException

from agent.slack import channel_options


class _FakeClient:
    token = "xoxb-test"

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    async def conversations_list(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def _install(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    @asynccontextmanager
    async def fake_slack_client(**_kwargs: Any):
        yield client

    monkeypatch.setattr(channel_options, "slack_client", fake_slack_client)


async def test_lists_every_page_sorted_by_name_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        [
            {
                "channels": [
                    {
                        "id": "C2",
                        "name": "oss-help",
                        "is_private": False,
                        "is_member": True,
                        "num_members": 2310,
                    },
                    {"id": "C9", "name": "", "is_member": True},
                    "junk",
                ],
                "response_metadata": {"next_cursor": "page-2"},
            },
            {
                "channels": [
                    {
                        "id": "G1",
                        "name": "oss-maintainers",
                        "is_private": True,
                        "is_member": True,
                        "is_ext_shared": False,
                        "num_members": 41,
                    },
                    {"id": "C3", "name": "community", "is_member": False, "num_members": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        ]
    )
    _install(monkeypatch, client)

    listed = await channel_options.list_slack_channels()

    assert [option.id for option in listed] == ["C3", "C2", "G1"]
    assert listed[1].num_members == 2310 and listed[1].is_member is True
    assert listed[2].is_private is True
    # A boolean where Slack promised a count is not a count.
    assert listed[0].num_members is None and listed[0].is_member is False
    assert client.calls[0]["types"] == "public_channel,private_channel"
    assert client.calls[0]["exclude_archived"] is True
    assert client.calls[1]["cursor"] == "page-2"

    # The directory is a suggestion list: a second browse within the TTL is served from cache.
    assert await channel_options.list_slack_channels() == listed
    assert len(client.calls) == 2


async def test_a_repeating_cursor_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {"channels": [], "response_metadata": {"next_cursor": "loop"}}
    _install(monkeypatch, _FakeClient([page, page, page]))

    with pytest.raises(HTTPException) as excinfo:
        await channel_options.list_slack_channels()

    assert excinfo.value.status_code == 502
