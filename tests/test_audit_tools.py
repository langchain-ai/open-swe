from typing import Literal
from uuid import uuid4

import pytest

from openswe.audit_logs import tools
from openswe.audit_logs.models import AuditLog
from openswe.run_config import RunConfig
from openswe.users import User


@pytest.mark.parametrize(
    ("result", "succeeded"),
    [
        ({"ok": False}, False),
        ({"success": False}, False),
        ({"error": "returned-secret"}, False),
        ({"ok": True, "value": "returned-secret"}, True),
    ],
)
async def test_tool_audit_outcome_without_payloads(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, object], succeeded: bool
) -> None:
    entries: list[AuditLog] = []
    user = User(id=uuid4())
    monkeypatch.setattr(
        tools,
        "configurable",
        lambda: RunConfig(source="dashboard", github_login="alice", thread_id="thread-id"),
    )

    async def lookup(*_args: object) -> User:
        return user

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    monkeypatch.setattr(User, "for_login", lookup)
    monkeypatch.setattr(tools, "append_safely", append)

    @tools.audit_tool()
    async def mutate(settings: dict[str, str]) -> dict[str, object]:
        return result

    assert await mutate({"token": "argument-secret"}) == result
    (entry,) = entries
    assert entry.operation_succeeded is succeeded
    assert entry.user_id == user.id
    assert entry.enrichments.actor_kind == "agent"
    assert entry.enrichments.thread_id == "thread-id"
    assert "argument-secret" not in entry.model_dump_json()
    assert "returned-secret" not in entry.model_dump_json()


async def test_tool_audit_records_exception_but_not_reads_or_background_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries: list[AuditLog] = []
    monkeypatch.setattr(
        tools,
        "configurable",
        lambda: RunConfig(source="schedule", github_login="schedule-owner"),
    )

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    monkeypatch.setattr(tools, "append_safely", append)

    @tools.audit_tool(skip_read=True)
    async def mutate(action: Literal["read", "set"]) -> dict[str, object]:
        if action == "set":
            raise ValueError("exception-secret")
        return {"ok": True}

    assert await mutate("read") == {"ok": True}
    assert entries == []
    with pytest.raises(ValueError, match="exception-secret"):
        await mutate("set")
    (entry,) = entries
    assert entry.operation_succeeded is False
    assert entry.user_id is None
    assert entry.enrichments.actor_login is None
    assert "exception-secret" not in entry.model_dump_json()


async def test_opted_in_skill_tool_records_policy_refusal_without_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.tools import access as access_module
    from openswe.tools import user_skills

    entries: list[AuditLog] = []
    monkeypatch.setattr(tools, "configurable", lambda: RunConfig(source="schedule"))

    async def refuse() -> access_module.Access:
        return access_module.Access()

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    monkeypatch.setattr(access_module, "resolve_access", refuse)
    monkeypatch.setattr(tools, "append_safely", append)
    result = await user_skills.save_user_skill("secret-skill", "secret-description")
    assert result["ok"] is False
    (entry,) = entries
    assert entry.operation_name == "save_user_skill"
    assert entry.operation_succeeded is False
    assert "secret" not in entry.model_dump_json()
