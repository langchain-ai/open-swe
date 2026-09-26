from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

from agent.run_config import RunConfig
from agent.tools import access as tool_access
from agent.tools.access import Policy, ack, resolve_access

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
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(
        tool_access, "fetch_slack_thread_messages", AsyncMock(side_effect=lambda *_: messages)
    )
    logins = {"UA": "alice", "UB": "bob"}
    monkeypatch.setattr(
        tool_access.User, "login_for_slack", AsyncMock(side_effect=lambda user: logins.get(user))
    )
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


@pytest.mark.parametrize("visibility", ["private", "unknown"])
async def test_private_owner_gets_full_results_and_unknown_scope_fails_closed(
    metadata: dict, visibility: str
) -> None:
    metadata.update(visibility=visibility, owner_login="alice")
    expected = "full" if visibility == "private" else None
    assert (await resolve_access(_cfg())).mode(_OWN) == expected


async def test_calls_are_rechecked_and_projected(monkeypatch: pytest.MonkeyPatch) -> None:
    @tool_access.access(_OWN)
    async def tool() -> dict[str, object]:
        return {"id": "x", "prompt": "secret"}

    resolved = AsyncMock(return_value=tool_access.Access(sole=True))
    monkeypatch.setattr(tool_access, "resolve_access", resolved)
    assert await tool() == {"ok": True, "id": "x"}
    resolved.return_value = tool_access.Access()
    assert (await tool())["ok"] is False
