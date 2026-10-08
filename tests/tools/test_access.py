from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

from openswe.run_config import RunConfig
from openswe.tools import access as tool_access
from openswe.tools.access import Policy, ack, resolve_access

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
