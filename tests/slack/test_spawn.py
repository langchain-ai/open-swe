from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import spawn
from agent.source_context import SlackThreadRef
from agent.spawn import (
    SpawnDestination,
    SpawnHandoff,
    SpawnOrigin,
    spawn_slack_session,
)


class _Threads:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []

    async def create(
        self, *, thread_id: str, if_exists: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        self.created.append({"thread_id": thread_id, "if_exists": if_exists, "metadata": metadata})
        return {"thread_id": thread_id}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updated.append({"thread_id": thread_id, "metadata": metadata})


class _Client:
    def __init__(self) -> None:
        self.threads = _Threads()


def _origin() -> SpawnOrigin:
    return SpawnOrigin.from_config(
        {
            "thread_id": "parent-thread",
            "github_login": "octocat",
            "user_email": "Octocat@Example.com",
            "agent_model_id": "claude-opus-5",
            "repo": {"owner": "acme", "name": "billing"},
        },
        {
            "channel_id": "C-origin",
            "thread_ts": "1717171717.000100",
            "triggering_user_id": "U1",
            "triggering_user_name": "Ramon",
            "triggering_user_email": "ramon@example.com",
            "triggering_user_timezone": "America/New_York",
            "triggering_event_ts": "1717171717.000200",
        },
    )


def _handoff(**overrides: Any) -> SpawnHandoff:
    return SpawnHandoff(
        title="Migrate the billing cron",
        content="Do the work",
        repo={"owner": "acme", "name": "billing"},
        **overrides,
    )


@pytest.fixture
def spawn_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    calls = {
        "bind_slack_thread_id": AsyncMock(),
        "delete_slack_thread_associations": AsyncMock(),
        "store_slack_run_mapping": AsyncMock(),
        "dispatch_agent_run": AsyncMock(return_value={"run_id": "run-1"}),
    }
    for name, mock in calls.items():
        monkeypatch.setattr(spawn, name, mock)
    monkeypatch.setattr(
        spawn, "dashboard_thread_url", lambda thread_id: f"https://web.example/{thread_id}"
    )
    return calls


async def test_spawn_binds_the_destination_to_a_fresh_session(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    client = _Client()
    session = await spawn_slack_session(
        client,  # type: ignore[arg-type]
        destination=SpawnDestination(channel_id="C-new", thread_ts="1717171718.000100"),
        origin=_origin(),
        handoff=_handoff(),
    )

    assert session.thread_id
    assert session.run_id == "run-1"
    assert session.dashboard_url == f"https://web.example/{session.thread_id}"
    assert spawn_calls["bind_slack_thread_id"].await_args_list[0].args == (
        client,
        "C-new",
        "1717171718.000100",
        session.thread_id,
    )
    assert client.threads.created[0]["if_exists"] == "do_nothing"
    assert client.threads.updated[0]["thread_id"] == session.thread_id


async def test_spawn_uses_a_thread_id_the_caller_already_claimed(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    """Slack's channel creation takes the session id, so the caller may claim it first."""
    session = await spawn_slack_session(
        _Client(),  # type: ignore[arg-type]
        destination=SpawnDestination(channel_id="C-new", thread_ts="0", surface="slack_channel"),
        origin=_origin(),
        handoff=_handoff(),
        thread_id="claimed-thread",
    )
    assert session.thread_id == "claimed-thread"


async def test_spawned_location_carries_the_surface_and_the_person(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    session = await spawn_slack_session(
        _Client(),  # type: ignore[arg-type]
        destination=SpawnDestination(channel_id="C-new", thread_ts="0", surface="slack_channel"),
        origin=_origin(),
        handoff=_handoff(),
    )

    location = session.slack_thread
    assert location.surface == "slack_channel"
    assert location.channel_id == "C-new"
    assert location.thread_ts == "0"
    assert location.triggering_event_ts == "0"
    assert location.triggering_user_id == "U1"
    assert location.triggering_user_name == "Ramon"
    assert location.triggering_user_email == "ramon@example.com"
    assert location.triggering_user_timezone == "America/New_York"


async def test_spawn_records_where_the_session_came_from(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    client = _Client()
    spawned_from = {"channel_id": "C-origin", "thread_id": "parent-thread"}
    session = await spawn_slack_session(
        client,  # type: ignore[arg-type]
        destination=SpawnDestination(channel_id="C-new", thread_ts="0", surface="slack_channel"),
        origin=_origin(),
        handoff=_handoff(source_context={"spawned_from": spawned_from}),
    )

    metadata = client.threads.updated[0]["metadata"]
    assert metadata["source"] == "slack"
    assert metadata["title"] == "Migrate the billing cron"
    assert metadata["repo"] == {"owner": "acme", "name": "billing"}
    assert metadata["repo_owner"] == "acme"
    assert metadata["github_login"] == "octocat"
    assert metadata["triggering_user_email"] == "octocat@example.com"
    assert metadata["source_context"]["spawned_from"] == spawned_from
    assert (
        metadata["source_context"]["slack_thread"]
        == SlackThreadRef.model_validate(session.slack_thread.dump()).dump()
    )


async def test_spawn_inherits_the_parents_run_configuration(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    await spawn_slack_session(
        _Client(),  # type: ignore[arg-type]
        destination=SpawnDestination(channel_id="C-new", thread_ts="0", surface="slack_channel"),
        origin=_origin(),
        handoff=_handoff(),
    )

    configurable = spawn_calls["dispatch_agent_run"].await_args_list[0].args[2]
    assert configurable["source"] == "slack"
    assert configurable["repo"] == {"owner": "acme", "name": "billing"}
    assert configurable["github_login"] == "octocat"
    assert configurable["agent_model_id"] == "claude-opus-5"
    assert configurable["slack_thread"]["channel_id"] == "C-new"


async def test_spawn_detaches_its_binding_when_the_first_run_fails(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    """The caller still owns the destination it created; the spawn undoes only its own work."""
    spawn_calls["dispatch_agent_run"].side_effect = RuntimeError("dispatch exploded")
    client = _Client()

    with pytest.raises(RuntimeError, match="dispatch exploded"):
        await spawn_slack_session(
            client,  # type: ignore[arg-type]
            destination=SpawnDestination(channel_id="C-new", thread_ts="0"),
            origin=_origin(),
            handoff=_handoff(),
        )

    detach = spawn_calls["delete_slack_thread_associations"].await_args_list[0]
    assert detach.args[1:] == ("C-new", "0")
    assert detach.kwargs["expected_thread_id"] == client.threads.created[0]["thread_id"]
    spawn_calls["store_slack_run_mapping"].assert_not_awaited()


async def test_spawn_leaves_an_unbound_destination_alone(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    spawn_calls["bind_slack_thread_id"].side_effect = RuntimeError("already mapped")

    with pytest.raises(RuntimeError, match="already mapped"):
        await spawn_slack_session(
            _Client(),  # type: ignore[arg-type]
            destination=SpawnDestination(channel_id="C-new", thread_ts="0"),
            origin=_origin(),
            handoff=_handoff(),
        )

    spawn_calls["delete_slack_thread_associations"].assert_not_awaited()
