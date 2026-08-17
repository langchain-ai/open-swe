from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.utils import thread_participants as participants


class _Threads:
    def __init__(self, metadata: dict[str, object]) -> None:
        self._metadata = metadata

    async def get(self, thread_id: str) -> dict[str, object]:
        assert thread_id == "thread-1"
        return {"metadata": self._metadata}


class _Client:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.threads = _Threads(metadata)


async def _active_mapping(login: str) -> dict[str, str]:
    return {"github_login": login, "status": "active"}


@pytest.mark.asyncio
async def test_resolves_slack_participants_from_verified_source_context() -> None:
    metadata = {
        "source": "slack",
        "participant_logins": ["owner"],
        "source_context": {
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"}
        },
    }
    with (
        patch.object(participants, "get_client", return_value=_Client(metadata)),
        patch.object(participants, "get_mapping", side_effect=_active_mapping),
        patch.object(
            participants,
            "fetch_slack_thread_messages",
            new_callable=AsyncMock,
            return_value=[{"user": "U2"}],
        ) as fetch,
        patch.object(
            participants,
            "login_for_slack_id",
            new_callable=AsyncMock,
            return_value="teammate",
        ),
    ):
        logins, unresolved, error = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "slack"}}
        )

    assert error is None
    assert unresolved == 0
    assert logins == {"owner", "teammate"}
    fetch.assert_awaited_once_with("C123", "1700000000.000100")


@pytest.mark.asyncio
async def test_resolves_linear_participants_from_issue_context() -> None:
    metadata = {
        "source": "linear",
        "participant_logins": ["owner"],
        "source_context": {"linear_issue": {"id": "lin-1"}},
    }
    with (
        patch.object(participants, "get_client", return_value=_Client(metadata)),
        patch.object(participants, "get_mapping", side_effect=_active_mapping),
        patch.object(
            participants,
            "fetch_linear_issue_participant_emails",
            new_callable=AsyncMock,
            return_value={"teammate@example.com"},
        ) as fetch,
        patch.object(
            participants,
            "login_for_email",
            new_callable=AsyncMock,
            return_value="teammate",
        ),
    ):
        logins, unresolved, error = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "linear"}}
        )

    assert error is None
    assert unresolved == 0
    assert logins == {"owner", "teammate"}
    fetch.assert_awaited_once_with("lin-1")


@pytest.mark.asyncio
async def test_resolves_github_participants_from_issue_context() -> None:
    metadata = {
        "source": "github",
        "participant_logins": ["owner"],
        "repo": {"owner": "acme", "name": "widgets"},
        "source_context": {"github_issue": {"number": 7}},
    }
    with (
        patch.object(participants, "get_client", return_value=_Client(metadata)),
        patch.object(participants, "get_mapping", return_value=None),
        patch.object(participants, "get_github_token", return_value="token"),
        patch.object(
            participants,
            "fetch_github_thread_participants",
            new_callable=AsyncMock,
            return_value={"owner", "teammate"},
        ) as fetch,
    ):
        logins, unresolved, error = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "github"}}
        )

    assert error is None
    assert unresolved == 0
    assert logins == {"owner", "teammate"}
    fetch.assert_awaited_once_with({"owner": "acme", "name": "widgets"}, 7, token="token")


@pytest.mark.asyncio
async def test_source_fetch_failure_does_not_fall_back_to_metadata_owner() -> None:
    metadata = {
        "source": "slack",
        "participant_logins": ["owner"],
        "source_context": {
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"}
        },
    }
    with (
        patch.object(participants, "get_client", return_value=_Client(metadata)),
        patch.object(participants, "get_mapping", side_effect=_active_mapping),
        patch.object(
            participants,
            "fetch_slack_thread_messages",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        logins, unresolved, error = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "slack"}}
        )

    assert logins is None
    assert unresolved == 0
    assert error == "Could not verify Slack thread participants"
