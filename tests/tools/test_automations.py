from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.run_config import RunConfig
from openswe.tools import automations


@pytest.fixture(autouse=True)
def admin(monkeypatch, grant_tool_access: Callable[..., None]) -> None:  # noqa: ANN001
    grant_tool_access(admin=True, admin_thread=True)
    monkeypatch.setattr(automations, "actor_is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        automations,
        "configurable",
        lambda: RunConfig(github_login="alice", user_email="alice@example.com"),
    )


async def test_create_automation_uses_trusted_admin_identity(monkeypatch) -> None:  # noqa: ANN001
    called: dict[str, Any] = {}

    async def create(login: str, body: Any, **kwargs: Any) -> dict[str, Any]:
        called.update(login=login, body=body, kwargs=kwargs)
        return {"id": "schedule-1", "scope": "workspace"}

    monkeypatch.setattr(automations.schedules, "create_agent_schedule", create)

    result = await automations.create_automation(
        "Check open pull requests",
        workspace="default",
        triggers=[
            automations.schedules.GitHubTrigger(
                repo="langchain-ai/open-swe", events=["pull_request.closed"]
            )
        ],
    )

    assert result["ok"] is True
    assert called["login"] == "alice"
    assert called["kwargs"] == {
        "email": "alice@example.com",
        "allow_admin_thread": True,
        "use_workspace_credentials": False,
    }
    assert called["body"].triggers[0].repo == "langchain-ai/open-swe"


async def test_automation_tools_recheck_admin(grant_tool_access: Callable[..., None]) -> None:
    grant_tool_access()

    result = await automations.delete_automation("schedule-1")

    assert result["ok"] is False
    assert "not available in this thread" in str(result["error"])


async def test_sole_writer_sees_only_an_acknowledgement(
    monkeypatch,  # noqa: ANN001
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(admin=True, sole=True)

    async def update(*_: Any, **__: Any) -> dict[str, Any]:
        return {"id": "schedule-1", "prompt": "Secret workspace prompt", "repo": "x/y"}

    monkeypatch.setattr(automations.schedules, "update_agent_schedule", update)

    result = await automations.update_automation("schedule-1", prompt="New prompt")

    assert result == {"ok": True, "automation": {"id": "schedule-1"}}


async def test_member_can_create_from_own_private_thread(monkeypatch, grant_tool_access) -> None:  # noqa: ANN001
    grant_tool_access(private=True, owner=True)
    monkeypatch.setattr(automations, "actor_is_admin", AsyncMock(return_value=False))
    create = AsyncMock(return_value={"id": "schedule-1"})
    monkeypatch.setattr(automations.schedules, "create_agent_schedule", create)
    result = await automations.create_automation(
        "Report", "default", [automations.schedules.ScheduleTrigger(cron="0 9 * * *")]
    )
    assert result["ok"] is True
    assert create.call_args.kwargs["allow_admin_thread"] is False
