from typing import Any

import pytest

from agent.run_config import RunConfig
from agent.tools import automations


@pytest.fixture(autouse=True)
def admin(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(automations, "require_admin", lambda action: None)
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
        "Check open pull requests", "0 9 * * 1-5", repo="langchain-ai/open-swe"
    )

    assert result["ok"] is True
    assert called["login"] == "alice"
    assert called["kwargs"] == {
        "email": "alice@example.com",
        "allow_admin_thread": True,
    }
    assert called["body"].repo == "langchain-ai/open-swe"


async def test_automation_tools_recheck_admin(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        automations, "require_admin", lambda action: "Only workspace admins can manage automations."
    )

    result = await automations.delete_automation("schedule-1")

    assert result == {
        "ok": False,
        "error": "Only workspace admins can manage automations.",
    }
