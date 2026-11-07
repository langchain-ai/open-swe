from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.audit_logs import tools as audit_tools
from openswe.audit_logs.models import AuditLog
from openswe.run_config import RunConfig
from openswe.slack.allowed_bots import AllowedSlackBot
from openswe.tools import access as tool_access
from openswe.tools import slack_bots
from openswe.tools.access import Access


@pytest.fixture
async def audit(monkeypatch: pytest.MonkeyPatch) -> list[AuditLog]:
    entries: list[AuditLog] = []
    monkeypatch.setattr(audit_tools, "_enrich", AsyncMock())
    monkeypatch.setattr(audit_tools, "append_safely", AsyncMock(side_effect=entries.append))
    return entries


@pytest.mark.parametrize(
    "scope",
    [
        Access(),
        Access(admin=True),
        Access(admin_surface=True, direct=True),
        Access(admin=True, admin_surface=True),
    ],
)
async def test_unauthorized_calls_cannot_read_or_mutate_bots(
    monkeypatch: pytest.MonkeyPatch, audit: list[AuditLog], scope: Access
) -> None:
    monkeypatch.setattr(tool_access, "resolve_access", AsyncMock(return_value=scope))
    add = AsyncMock()
    remove = AsyncMock()
    listing = AsyncMock()
    monkeypatch.setattr(slack_bots, "allow_bot", add)
    monkeypatch.setattr(slack_bots.AllowedSlackBot, "remove", remove)
    monkeypatch.setattr(slack_bots, "available_bots", listing)
    assert (await slack_bots.list_slack_bots())["ok"] is False
    assert (await slack_bots.allow_slack_bot("B123"))["ok"] is False
    assert (await slack_bots.remove_slack_bot("T123", "B123"))["ok"] is False
    add.assert_not_awaited()
    remove.assert_not_awaited()
    listing.assert_not_awaited()
    assert len(audit) == 2
    assert all(entry.operation_succeeded is False for entry in audit)


async def test_sole_writer_changes_are_redacted_and_rechecked(
    monkeypatch: pytest.MonkeyPatch, audit: list[AuditLog]
) -> None:
    scope = AsyncMock(return_value=Access(admin=True, sole=True, direct=True))
    monkeypatch.setattr(tool_access, "resolve_access", scope)
    monkeypatch.setattr(slack_bots, "configurable", lambda: RunConfig(github_login="alice"))
    bot = AllowedSlackBot(
        team_id="T123", bot_id="B123", name="Release bot", created_at=datetime.now(UTC)
    )
    add = AsyncMock(return_value=bot)
    remove = AsyncMock()
    monkeypatch.setattr(slack_bots, "allow_bot", add)
    monkeypatch.setattr(slack_bots.AllowedSlackBot, "remove", remove)
    assert (await slack_bots.list_slack_bots())["ok"] is False
    assert await slack_bots.allow_slack_bot(" U123 ") == {"ok": True}
    assert add.await_args is not None
    assert add.await_args.args[0].bot_id == "U123"
    assert add.await_args.args[1] == {"sub": "alice"}
    assert await slack_bots.remove_slack_bot("T123", "B123") == {"ok": True}
    scope.return_value = Access(admin=True, direct=True)
    assert (await slack_bots.remove_slack_bot("T123", "B123"))["ok"] is False
    assert remove.await_count == 1
    assert [entry.operation_succeeded for entry in audit] == [True, True, False]


async def test_private_admin_sees_bots_and_validation_errors_are_audited(
    monkeypatch: pytest.MonkeyPatch, audit: list[AuditLog]
) -> None:
    monkeypatch.setattr(
        tool_access,
        "resolve_access",
        AsyncMock(return_value=Access(admin=True, admin_surface=True, direct=True)),
    )
    monkeypatch.setattr(slack_bots, "configurable", lambda: RunConfig(github_login="alice"))
    bot = AllowedSlackBot(
        team_id="T123", bot_id="B123", name="Release bot", created_at=datetime.now(UTC)
    )
    monkeypatch.setattr(slack_bots, "available_bots", AsyncMock(return_value=[]))
    monkeypatch.setattr(slack_bots.AllowedSlackBot, "all", AsyncMock(return_value=[bot]))
    assert await slack_bots.list_slack_bots() == {
        "available": [],
        "allowed": [bot.model_dump(mode="json")],
    }
    add = AsyncMock(side_effect=HTTPException(409, "This bot is already allowed."))
    monkeypatch.setattr(slack_bots, "allow_bot", add)
    assert await slack_bots.allow_slack_bot("B123") == {
        "ok": False,
        "error": "This bot is already allowed.",
    }
    assert (await slack_bots.allow_slack_bot("not-an-id"))["ok"] is False
    assert add.await_count == 1
    assert all(entry.operation_succeeded is False for entry in audit)
