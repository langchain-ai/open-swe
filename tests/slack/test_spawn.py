from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.slack import spawn
from agent.slack.spawn import (
    SpawnDestination,
    SpawnHandoff,
    SpawnOrigin,
    spawn_slack_session,
)
from agent.slack.surfaces import channel as surfaces_channel
from agent.source_context import SlackThreadRef


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
        **{
            "title": "Migrate the billing cron",
            "content": "Do the work",
            "repo": {"owner": "acme", "name": "billing"},
            **overrides,
        }
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


@pytest.fixture
def opening(monkeypatch: pytest.MonkeyPatch, spawn_calls: dict[str, AsyncMock]) -> dict[str, Any]:
    """Slack agrees to make the channel, and the webhook has not bound it yet."""
    calls: dict[str, Any] = {
        "create_code_channel": AsyncMock(return_value=("C-code", None)),
        "resolve_slack_thread_id": AsyncMock(return_value="thread-code"),
        "archive_code_channel": AsyncMock(return_value=(True, None)),
        "invite_to_slack_channel": AsyncMock(return_value=(1, "")),
    }
    for name, mock in calls.items():
        monkeypatch.setattr(spawn, name, mock)
    chrome = {
        "set_session_status": AsyncMock(return_value=True),
        "set_context_bar": AsyncMock(return_value=(True, None)),
        "set_commands": AsyncMock(return_value=({"ok": True}, None)),
    }
    for name, mock in chrome.items():
        monkeypatch.setattr(surfaces_channel, name, mock)
    return {**calls, **chrome, **spawn_calls}


async def _open(**overrides: Any) -> Any:
    return await spawn.open_code_channel(
        _Client(),  # type: ignore[arg-type]
        **{
            "title": "Migrate the billing cron",
            "content": "Do the work",
            "repo": {"owner": "acme", "name": "billing"},
            "origin": _origin(),
            "invite": ["U1"],
            **overrides,
        },
    )


async def test_opening_a_channel_claims_the_id_the_webhook_derives(
    opening: dict[str, Any],
) -> None:
    """Slack's first event in the new channel binds it; both sides land on one id."""
    opened = await _open()

    assert opened.channel_id == "C-code"
    assert opened.session.thread_id == "thread-code"
    assert opening["resolve_slack_thread_id"].await_args.args[1:] == ("C-code", "0")
    assert opening["dispatch_agent_run"].await_args.args[0] == "thread-code"


async def test_opening_a_channel_dresses_it_before_the_run_starts(
    opening: dict[str, Any],
) -> None:
    """The run's completion returns the session to idle, so `processing` goes first."""
    opened = await _open()

    opening["set_session_status"].assert_awaited_once_with("C-code", "processing")
    channel, items = opening["set_context_bar"].await_args.args
    assert (channel, items[0]["label"]) == ("C-code", "acme/billing")
    assert opening["set_commands"].await_args.args == (
        "C-code",
        surfaces_channel.DEFAULT_CODE_CHANNEL_COMMANDS,
    )
    assert opened.warnings == []


async def test_chrome_that_will_not_apply_does_not_stop_the_session(
    opening: dict[str, Any],
) -> None:
    opening["set_context_bar"].side_effect = RuntimeError("slack said no")

    opened = await _open()

    assert opened.session.thread_id == "thread-code"
    assert "slack said no" in opened.warnings[0]


@pytest.mark.parametrize(
    ("origin_channel_id", "origin_message_ts", "expected"),
    [
        ("C-origin", "1717171717.000200", ("C-origin", "1717171717.000200")),
        # The pair goes in together or not at all.
        ("C-origin", "", ("", "")),
        ("", "1717171717.000200", ("", "")),
        ("", "", ("", "")),
    ],
    ids=["both", "no message", "no channel", "neither"],
)
async def test_the_origin_pair_is_passed_only_when_it_is_a_pair(
    opening: dict[str, Any],
    origin_channel_id: str,
    origin_message_ts: str,
    expected: tuple[str, str],
) -> None:
    await _open(origin_channel_id=origin_channel_id, origin_message_ts=origin_message_ts)

    kwargs = opening["create_code_channel"].await_args.kwargs
    assert (kwargs["origin_channel_id"], kwargs["origin_message_ts"]) == expected


async def test_a_channel_slack_refuses_is_reported_not_raised(opening: dict[str, Any]) -> None:
    opening["create_code_channel"].return_value = (None, "name_taken")

    with pytest.raises(spawn.CodeChannelError, match="name_taken") as failure:
        await _open()

    assert failure.value.retryable is False
    opening["dispatch_agent_run"].assert_not_awaited()


@pytest.mark.parametrize("failing", ["resolve_slack_thread_id", "dispatch_agent_run"])
async def test_a_channel_with_no_session_is_archived_rather_than_left_open(
    opening: dict[str, Any], failing: str
) -> None:
    opening[failing].side_effect = RuntimeError("it exploded")

    with pytest.raises(spawn.CodeChannelError, match="it exploded") as failure:
        await _open()

    assert failure.value.retryable is True
    opening["archive_code_channel"].assert_awaited_once_with("C-code")


async def test_opening_a_channel_puts_the_named_people_in_it(opening: dict[str, Any]) -> None:
    """A channel nobody is in is a channel nobody reads."""
    opened = await _open(invite=["U1", "<@U2>", "u3", "U1"])

    # Mentions unwrapped, case normalized, duplicates dropped.
    assert opening["invite_to_slack_channel"].await_args.args == ("C-code", ["U1", "U2", "U3"])
    assert opened.invited == ["U1", "U2", "U3"]
    assert opened.warnings == []


@pytest.mark.parametrize("invite", [[], [""], ["not-an-id"], ["<@>"]])
async def test_a_channel_needs_at_least_one_person(
    opening: dict[str, Any], invite: list[str]
) -> None:
    with pytest.raises(spawn.CodeChannelError, match="at least one Slack user"):
        await _open(invite=invite)

    opening["create_code_channel"].assert_not_awaited()


async def test_an_invite_slack_refuses_is_a_warning_not_a_failure(
    opening: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The work still starts; a public channel is reachable by its link."""
    monkeypatch.setattr(
        spawn, "invite_to_slack_channel", AsyncMock(return_value=(0, "missing_scope"))
    )

    opened = await _open()

    assert opened.session.thread_id == "thread-code"
    assert opened.invited == []
    assert "missing_scope" in opened.warnings[0]


async def test_a_handed_over_title_is_marked_as_replaceable(
    spawn_calls: dict[str, AsyncMock],
) -> None:
    """Title generation only replaces a title that still matches its seed."""
    client = _Client()
    await spawn_slack_session(
        client,  # type: ignore[arg-type]
        destination=SpawnDestination(channel_id="C-new", thread_ts="0", surface="slack_channel"),
        origin=_origin(),
        handoff=_handoff(title="fix the flaky login test"),
    )

    metadata = client.threads.updated[0]["metadata"]
    assert metadata["title"] == "fix the flaky login test"
    assert metadata["title_seed"] == metadata["title"]
