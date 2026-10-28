from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

from openswe.run_config import RunConfig
from openswe.tools import access as tool_access
from openswe.tools.access import Policy, ack, resolve_access
from tests.support.slack_api import SlackAPI

_OWN = Policy(trusted="private", actor="owner", sole=ack("id"))
_SLACK = {"channel_id": "C1", "thread_ts": "1.0"}


@pytest.fixture
def metadata(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    saved: dict[str, object] = {
        "visibility": "public",
        "owner_type": "user",
        "participant_logins": ["alice"],
    }
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _id: {"metadata": saved}))
        ),
    )
    return saved


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [{"user": "UA"}, {"user": "UBOT"}]
    fetch = AsyncMock(side_effect=lambda *_, **__: messages)
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    logins = {"UA": "alice", "UB": "bob"}
    monkeypatch.setattr(
        tool_access.User, "login_for_slack", AsyncMock(side_effect=lambda user: logins.get(user))
    )
    monkeypatch.setattr(tool_access, "fetch_slack_thread_messages", fetch)
    return messages


def _cfg(**fields: object) -> RunConfig:
    return RunConfig.model_validate(
        {"thread_id": "t-1", "source": "dashboard", "github_login": "alice", **fields}
    )


async def test_the_only_writer_of_a_shared_thread_gets_acknowledgements(metadata: dict) -> None:
    access = await resolve_access(_cfg())
    assert access.mode(_OWN) == "sole"
    assert access.mode(Policy(trusted="private", actor="owner")) is None
    assert _OWN.sole is not None
    assert _OWN.sole({"id": "x", "prompt": "secret"}) == {"ok": True, "id": "x"}


@pytest.mark.parametrize(
    "change",
    [
        {"participant_logins": ["alice", "bob"]},
        {"participant_emails": ["bob@example.com"]},
        {"owner_type": "system"},
        {"source_context": {"github_issue": {"owner": "o", "repo": "r", "number": 1}}},
    ],
)
async def test_another_writer_or_non_personal_thread_loses_the_tools(
    metadata: dict, change: dict
) -> None:
    metadata.update(change)
    assert (await resolve_access(_cfg())).mode(_OWN) is None


async def test_automatic_runs_are_never_sole_writers(metadata: dict) -> None:
    assert (await resolve_access(_cfg(schedule_id="s-1"))).mode(_OWN) is None


async def test_slack_thread_counts_every_human_poster(metadata: dict, slack: list) -> None:
    cfg = _cfg(source="slack", slack_thread=_SLACK)
    assert (await resolve_access(cfg)).mode(_OWN) == "sole"
    slack.append({"user": "UB"})
    assert (await resolve_access(cfg)).mode(_OWN) is None
    slack[-1] = {"user": "UC", "bot_id": "B1"}
    assert (await resolve_access(cfg)).mode(_OWN) is None


@pytest.mark.parametrize(
    "scope, expected",
    [
        ({"visibility": "private"}, "full"),
        ({"visibility": "unknown"}, None),
        ({"visibility": "private", "owner_type": "unknown"}, None),
        ({"visibility": "private", "owner_type": "system"}, None),
        ({"visibility": "private", "admin_thread": "true"}, None),
    ],
)
async def test_private_owner_gets_full_results_and_unknown_scope_fails_closed(
    metadata: dict[str, object], scope: dict[str, object], expected: tool_access.Mode | None
) -> None:
    metadata.update(owner_login="alice", **scope)
    resolved = await resolve_access(_cfg(admin_thread=True))
    assert resolved.mode(_OWN) == expected
    if expected is None:
        assert resolved == tool_access.Access()


@pytest.mark.parametrize("admin", [False, True])
async def test_mcp_caller_owns_their_surface_but_only_admins_get_admin_places(
    metadata: dict[str, object], monkeypatch: pytest.MonkeyPatch, admin: bool
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice" if admin else "carol")
    caller = await resolve_access(_cfg(thread_id=None, source="mcp", admin_thread=True))
    assert caller.mode(_OWN) == "full"
    for place in ("admin_thread", "admin_surface"):
        assert (caller.mode(Policy(trusted=place, actor="admin")) is not None) is admin
    # A thread run that claims the MCP source is still judged by its thread.
    assert (await resolve_access(_cfg(source="mcp"))).mode(_OWN) is None


@pytest.mark.parametrize("place", ["private", "admin_thread", "admin_surface"])
async def test_sharing_revokes_tools_despite_stale_private_admin_run_config(
    metadata: dict[str, object], monkeypatch: pytest.MonkeyPatch, place: tool_access.Place
) -> None:
    metadata.update(visibility="private", owner_login="alice", admin_thread=True)
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")
    monkeypatch.setattr(tool_access, "configurable", lambda: _cfg(admin_thread=True))
    calls: list[str] = []

    @tool_access.access(Policy(trusted=place, actor="admin"))
    async def tool() -> dict[str, object]:
        calls.append("called")
        return {"secret": "private data"}

    assert await tool() == {"secret": "private data"}
    metadata.update(visibility="public", admin_thread=False)
    assert (await tool())["ok"] is False
    assert calls == ["called"]


async def test_calls_are_rechecked_and_projected(monkeypatch: pytest.MonkeyPatch) -> None:
    @tool_access.access(_OWN)
    async def tool() -> dict[str, object]:
        return {"id": "x", "prompt": "secret"}

    resolved = AsyncMock(return_value=tool_access.Access(sole=True))
    monkeypatch.setattr(tool_access, "resolve_access", resolved)
    assert await tool() == {"ok": True, "id": "x"}
    resolved.return_value = tool_access.Access()
    assert (await tool())["ok"] is False


async def test_unreadable_slack_history_is_not_sole(
    metadata: dict, slack: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = AsyncMock(side_effect=RuntimeError("page 2 failed"))
    monkeypatch.setattr(tool_access, "fetch_slack_thread_messages", fetch)
    cfg = _cfg(source="slack", slack_thread=_SLACK)
    assert (await resolve_access(cfg)).mode(_OWN) is None
    assert fetch.await_args is not None
    assert fetch.await_args.kwargs == {"complete": True}


@pytest.mark.parametrize(
    "scope, run_fields",
    [
        ({"owner_type": "user"}, {}),
        ({"visibility": "private"}, {}),
        ({"source_context": {}}, {}),
        ({}, {"source": "dashboard"}),
        ({}, {"slack_thread": {**_SLACK, "triggering_bot_id": "OTHER"}}),
        ({}, {"slack_thread": {**_SLACK, "channel_id": "C2", "triggering_bot_id": "B1"}}),
    ],
)
async def test_bot_channel_history_requires_matching_system_thread_context(
    metadata: dict[str, object], scope: dict[str, object], run_fields: dict[str, object]
) -> None:
    slack = {**_SLACK, "triggering_bot_id": "B1"}
    metadata.update(owner_type="system", source_context={"slack_thread": slack})
    metadata.update(scope)
    cfg = _cfg(**{"source": "slack", "slack_thread": slack, "github_login": None, **run_fields})
    resolved = await resolve_access(cfg)
    assert resolved.mode(Policy(trusted="channel_history")) is None


async def test_bot_channel_history_is_scoped_and_revocable(
    metadata: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    slack_api: SlackAPI,
) -> None:
    from openswe.slack.tools.read_channel_messages import slack_read_channel_messages

    slack = {**_SLACK, "triggering_bot_id": "B1", "team_id": "T1"}
    metadata.update(owner_type="system", source_context={"slack_thread": slack})
    cfg = _cfg(source="slack", slack_thread=slack, github_login=None)
    monkeypatch.setattr(tool_access, "configurable", lambda: cfg)
    resolved = await resolve_access(cfg)
    assert not resolved.private
    assert resolved.mode(_OWN) is None

    refused = await slack_read_channel_messages("C2")
    assert refused["success"] is False
    assert not slack_api.calls

    slack_api.respond(
        {
            "ok": True,
            "channel": {
                "id": "C1",
                "is_channel": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            },
        }
    )
    slack_api.respond({"ok": True, "messages": [{"ts": "0.5", "text": "Earlier alert"}]})
    assert (await slack_read_channel_messages("C1"))["success"] is True

    metadata["source_context"] = {}
    assert (await slack_read_channel_messages("C1"))["ok"] is False
