from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.input_messages import PersonIdentity
from agent.users import User
from agent.utils import thread_participants as participants


@pytest.mark.asyncio
async def test_participant_ingress_upgrades_legacy_and_preserves_uncovered_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = User()
    monkeypatch.setattr(User, "for_person", AsyncMock(return_value=alice))
    metadata: dict[str, object] = {
        participants.PARTICIPANT_LOGINS_KEY: ["Alice"],
        participants.PARTICIPANT_EMAILS_KEY: {"alice@example.com": True},
    }
    assert len(participants.participant_ids(metadata)) == 2
    metadata.update(await participants.participant_metadata(metadata))
    assert participants.participant_ids(metadata) == {f"user:{alice.id}"}
    metadata[participants.PARTICIPANT_LOGINS_KEY] = {"alice": True, "bob": True}
    assert participants.participant_ids(metadata) == {f"user:{alice.id}", "github:bob"}


@pytest.mark.asyncio
async def test_external_participants_link_without_losing_historical_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice, bob = User(), User()
    known: dict[str, User] = {"github:alice": alice}

    async def resolve(person: PersonIdentity) -> User | None:
        return known.get(person["id"])

    monkeypatch.setattr(User, "for_person", resolve)
    metadata = await participants.participant_metadata(
        {}, login="alice", people=[{"id": "slack:U1"}, {"id": "slack:U2"}]
    )
    assert participants.participant_ids(metadata) == {f"user:{alice.id}", "slack:U1", "slack:U2"}
    known.update({"slack:U1": alice, "slack:U2": bob})
    metadata.update(await participants.participant_metadata(metadata))
    assert participants.participant_ids(metadata) == {f"user:{alice.id}", f"user:{bob.id}"}
    known["slack:U2"] = alice
    metadata.update(await participants.participant_metadata(metadata, people=[{"id": "slack:U2"}]))
    assert participants.participant_ids(metadata) == {f"user:{alice.id}", f"user:{bob.id}"}
    monkeypatch.setattr(
        User, "for_person", AsyncMock(side_effect=RuntimeError("Database unavailable"))
    )
    metadata.update(await participants.participant_metadata(metadata, login="alice"))
    assert participants.participant_ids(metadata) == {f"user:{alice.id}", f"user:{bob.id}"}


@pytest.mark.asyncio
async def test_concurrent_participant_updates_do_not_erase_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice, bob = User(), User()
    monkeypatch.setattr(User, "for_person", AsyncMock(side_effect=[alice, bob]))
    first = await participants.participant_metadata({}, login="alice")
    second = await participants.participant_metadata({}, login="bob")
    assert participants.participant_ids({**first, **second}) == {
        f"user:{alice.id}",
        f"user:{bob.id}",
    }


class _Threads:
    def __init__(self, metadata: dict[str, object]) -> None:
        self._metadata = metadata

    async def get(self, thread_id: str) -> dict[str, object]:
        assert thread_id == "thread-1"
        return {"metadata": self._metadata}


class _Client:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.threads = _Threads(metadata)


async def _known_user(provider: str, login: str) -> SimpleNamespace:
    return SimpleNamespace(github_login=login)


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
        patch.object(participants.User, "for_login", side_effect=_known_user),
        patch.object(
            participants,
            "fetch_slack_thread_messages",
            new_callable=AsyncMock,
            return_value=[{"user": "U2"}],
        ) as fetch,
        patch.object(
            participants.User,
            "login_for_slack",
            new_callable=AsyncMock,
            return_value="teammate",
        ),
    ):
        logins, unresolved = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "slack"}}
        )

    assert unresolved == 0
    assert logins == {"owner", "teammate"}
    fetch.assert_awaited_once_with("C123", "1700000000.000100")


@pytest.mark.asyncio
async def test_resolves_linear_participants_from_metadata() -> None:
    metadata = {
        "source": "linear",
        "participant_logins": ["owner"],
        "source_context": {"linear_issue": {"id": "lin-1"}},
    }
    with (
        patch.object(participants, "get_client", return_value=_Client(metadata)),
        patch.object(participants.User, "for_login", side_effect=_known_user),
    ):
        logins, unresolved = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "linear"}}
        )

    assert unresolved == 0
    assert logins == {"owner"}


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
        patch.object(participants, "resolve_thread_github_token", return_value="token"),
        patch.object(
            participants,
            "fetch_github_thread_participants",
            new_callable=AsyncMock,
            return_value={"owner", "teammate"},
        ) as fetch,
    ):
        logins, unresolved = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "github"}}
        )

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
        patch.object(participants.User, "for_login", side_effect=_known_user),
        patch.object(
            participants,
            "fetch_slack_thread_messages",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        with pytest.raises(ValueError, match="Could not verify Slack thread participants"):
            await participants.resolve_thread_participant_logins(
                {"configurable": {"thread_id": "thread-1", "source": "slack"}}
            )


@pytest.mark.asyncio
async def test_rejects_acting_for_another_verified_participant() -> None:
    config = {"configurable": {"github_login": "attacker"}}
    with patch.object(participants, "get_config", return_value=config):
        with pytest.raises(ValueError, match="must match the user"):
            await participants.resolve_participant("victim")
