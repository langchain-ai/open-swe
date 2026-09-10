from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from agent.run_config import RunConfig
from agent.source_context import SlackThreadRef
from agent.tools import automations


@pytest.fixture(autouse=True)
def identity(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        automations,
        "configurable",
        lambda: RunConfig(github_login="alice", user_email="alice@example.com"),
    )

    @asynccontextmanager
    async def request_lock(_request_id: str) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(automations.automation_requests, "automation_request_lock", request_lock)


async def test_admin_create_automation_creates_immediately(monkeypatch) -> None:  # noqa: ANN001
    called: dict[str, Any] = {}

    async def create(login: str, body: Any, **kwargs: Any) -> dict[str, Any]:
        called.update(login=login, body=body, kwargs=kwargs)
        return {"id": "schedule-1", "scope": "workspace"}

    monkeypatch.setattr(automations, "is_admin", lambda email, login: True)
    monkeypatch.setattr(automations.schedules, "create_agent_schedule", create)

    result = await automations.create_automation(
        "Check open pull requests", "0 9 * * 1-5", repo="langchain-ai/open-swe"
    )

    assert result["ok"] is True
    assert result["automation"]["id"] == "schedule-1"
    assert called["login"] == "alice"
    assert called["kwargs"] == {
        "email": "alice@example.com",
        "allow_admin_thread": True,
    }
    assert called["body"].repo == "langchain-ai/open-swe"


async def test_non_admin_create_automation_queues_request_with_channel_name(
    monkeypatch, fake_store
) -> None:  # noqa: ANN001
    monkeypatch.setattr(automations, "is_admin", lambda email, login: False)
    monkeypatch.setattr(
        automations,
        "configurable",
        lambda: RunConfig(
            github_login="alice",
            user_email="alice@example.com",
            slack_thread=SlackThreadRef(
                triggering_user_id="U12345678", triggering_user_name="Alice Person"
            ),
        ),
    )

    async def channel_context(channel_id: str) -> dict[str, Any]:
        assert channel_id == "C12345678"
        return {
            "name": "team-open-swe",
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        }

    monkeypatch.setattr(automations, "get_slack_channel_context", channel_context)

    result = await automations.create_automation(
        "Check open pull requests",
        "0 9 * * 1-5",
        slack_channel_id="C12345678",
    )

    assert result["ok"] is True
    assert result["requested"] is True
    request = result["request"]
    assert request["status"] == "pending"
    assert request["requesterName"] == "Alice Person"
    assert request["automation"]["slackChannelName"] == "team-open-swe"
    stored = next(iter(fake_store.values(["automation_requests"]).values()))
    assert stored["requester_slack_id"] == "U12345678"


async def test_non_admin_request_rejects_external_slack_channel(monkeypatch, fake_store) -> None:  # noqa: ANN001
    monkeypatch.setattr(automations, "is_admin", lambda email, login: False)

    async def channel_context(channel_id: str) -> dict[str, Any]:
        return {
            "name": "shared-channel",
            "is_ext_shared": True,
            "is_pending_ext_shared": False,
        }

    monkeypatch.setattr(automations, "get_slack_channel_context", channel_context)

    result = await automations.create_automation(
        "Check open pull requests", "0 9 * * 1-5", slack_channel_id="C12345678"
    )

    assert result == {"ok": False, "error": "Slack channel must be internal to this workspace"}
    assert fake_store.values(["automation_requests"]) == {}


async def test_non_admin_cannot_request_admin_thread(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(automations, "is_admin", lambda email, login: False)

    result = await automations.create_automation("Manage workspace", "0 9 * * *", admin_thread=True)

    assert result == {
        "ok": False,
        "error": "Only workspace admins can request admin-thread automations.",
    }


async def test_approve_request_creates_as_requester_and_sends_dm(monkeypatch, fake_store) -> None:  # noqa: ANN001
    fake_store.seed(
        ["automation_requests"],
        "request-1",
        {
            "id": "request-1",
            "status": "pending",
            "requester_name": "Bob Person",
            "requested_by": "bob",
            "requester_email": "bob@example.com",
            "requester_slack_id": "U87654321",
            "automation": {
                "name": "Daily check",
                "prompt": "Check open pull requests",
                "schedule": "0 9 * * 1-5",
                "repo": None,
                "model_id": None,
                "effort": None,
                "slack_channel_id": None,
                "slack_notification_mode": "always",
                "admin_thread": False,
            },
            "slack_channel_name": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(automations, "require_admin", lambda action: None)
    created_as: dict[str, Any] = {}

    async def create(login: str, body: Any, **kwargs: Any) -> dict[str, Any]:
        created_as.update(login=login, body=body, kwargs=kwargs)
        return {"id": "schedule-1", "name": "Daily check"}

    messages: list[tuple[str, str]] = []

    async def send_dm(user_id: str, text: str) -> tuple[str | None, str | None]:
        messages.append((user_id, text))
        return "1.0", None

    monkeypatch.setattr(automations.schedules, "create_agent_schedule", create)
    monkeypatch.setattr(automations, "post_slack_dm", send_dm)

    result = await automations.approve_automation_request("request-1")

    assert result["ok"] is True
    assert created_as["login"] == "bob"
    assert created_as["kwargs"] == {
        "email": "bob@example.com",
        "allow_admin_thread": False,
    }
    assert messages == [
        (
            "U87654321",
            "Your Open SWE automation request *Daily check* was approved and created.",
        )
    ]
    assert fake_store.values(["automation_requests"])["request-1"]["status"] == "approved"


async def test_deny_request_records_message_and_sends_dm(monkeypatch, fake_store) -> None:  # noqa: ANN001
    fake_store.seed(
        ["automation_requests"],
        "request-1",
        {
            "id": "request-1",
            "status": "pending",
            "requester_name": "Bob Person",
            "requested_by": "bob",
            "requester_slack_id": "U87654321",
            "automation": {"name": "Daily check"},
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(automations, "require_admin", lambda action: None)
    messages: list[tuple[str, str]] = []

    async def send_dm(user_id: str, text: str) -> tuple[str | None, str | None]:
        messages.append((user_id, text))
        return "1.0", None

    monkeypatch.setattr(automations, "post_slack_dm", send_dm)

    result = await automations.deny_automation_request("request-1", "Use the shared report")

    assert result["ok"] is True
    assert result["request"]["denialMessage"] == "Use the shared report"
    assert messages == [
        (
            "U87654321",
            "Your Open SWE automation request *Daily check* was denied. Message: Use the shared report",
        )
    ]


async def test_automation_tools_recheck_admin(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        automations, "require_admin", lambda action: "Only workspace admins can manage automations."
    )

    result = await automations.delete_automation("schedule-1")

    assert result == {
        "ok": False,
        "error": "Only workspace admins can manage automations.",
    }
