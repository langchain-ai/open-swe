from collections.abc import Callable
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import schedules
from agent.dashboard.schedule_models import ScheduleCreateBody, ScheduleUpdateBody
from agent.scheduling import agent_schedules


def _stored_schedule(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "sched_1",
        "name": "Daily",
        "prompt": "Run daily",
        "schedule": "0 9 * * *",
        "repo": None,
        "model": "Default",
        "effort": None,
        "enabled": True,
        "cron_id": "cron_old",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def _existing_cron(fake_client: FakeLangGraphClient, cron_id: str = "cron_old") -> None:
    fake_client.crons.crons.append(
        {"cron_id": cron_id, "metadata": {"kind": "schedule", "key": "sched_1"}}
    )


@pytest.fixture
def fake_client(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> FakeLangGraphClient:
    client = patched_langgraph_client(schedules, agent_schedules)
    client.runs.run_id = "run_123"
    client.crons.cron_id = "cron_{n}"
    return client


@pytest.fixture
def auth(monkeypatch) -> None:  # noqa: ANN001
    async def fake_get_valid_access_token(login: str) -> str:
        return "gho_token"

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {"base_branch": "main", "branch_prefix": "open-swe"}

    async def fake_resolve_run_email(login: str, profile: dict[str, Any]) -> str:
        return "alice@example.com"

    async def fake_repo_config_for_user(login: str, full_name: str | None) -> dict[str, str] | None:
        if not full_name:
            return None
        owner, name = full_name.split("/", 1)
        return {"owner": owner, "name": name}

    async def fake_require_repo_access_for_user(login: str, full_name: str) -> str:
        return "gho_token"

    monkeypatch.setattr(schedules, "get_valid_access_token", fake_get_valid_access_token)
    monkeypatch.setattr(schedules, "get_profile", fake_get_profile)
    monkeypatch.setattr(schedules, "resolve_run_email", fake_resolve_run_email)
    monkeypatch.setattr(schedules, "repo_config_for_user", fake_repo_config_for_user)
    monkeypatch.setattr(
        agent_schedules, "require_repo_access_for_user", fake_require_repo_access_for_user
    )


def test_cron_validation_rejects_non_five_field_expression() -> None:
    with pytest.raises(ValidationError):
        ScheduleCreateBody(prompt="hello", schedule="0 9 * *")


def test_cron_validation_accepts_steps_ranges_and_lists() -> None:
    body = ScheduleCreateBody(prompt="hello", schedule="*/15 9-17 * * 1,3,5")

    assert body.schedule == "*/15 9-17 * * 1,3,5"


def test_slack_channel_validation_normalizes_ids() -> None:
    body = ScheduleCreateBody(
        prompt="hello", schedule="0 9 * * *", slack_channel_id=" c0123456789 "
    )

    assert body.slack_channel_id == "C0123456789"
    with pytest.raises(ValidationError):
        ScheduleCreateBody(prompt="hello", schedule="0 9 * * *", slack_channel_id="#general")


def test_slack_notification_mode_defaults_and_validates() -> None:
    default_body = ScheduleCreateBody(prompt="hello", schedule="0 9 * * *")
    conditional_body = ScheduleCreateBody(
        prompt="hello", schedule="0 9 * * *", slack_notification_mode="on_action"
    )

    assert default_body.slack_notification_mode == "always"
    assert conditional_body.slack_notification_mode == "on_action"
    with pytest.raises(ValidationError):
        ScheduleCreateBody.model_validate(
            {
                "prompt": "hello",
                "schedule": "0 9 * * *",
                "slack_notification_mode": "sometimes",
            }
        )


async def test_create_agent_schedule_registers_scheduler_cron(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    body = ScheduleCreateBody(
        name="Daily report",
        prompt="Summarize merged PRs",
        schedule="0 9 * * 1-5",
        repo="langchain-ai/open-swe",
        slack_channel_id="C0123456789",
    )

    result = await schedules.create_agent_schedule("alice", body, email="alice@example.com")

    assert result["name"] == "Daily report"
    assert result["enabled"] is True
    assert result["slackChannelId"] == "C0123456789"
    assert result["slackNotificationMode"] == "always"
    assert result["cronId"] == "cron_1"
    created = fake_client.crons.created[0]
    assert created["assistant_id"] == "scheduler"
    assert created["schedule"] == "0 9 * * 1-5"
    assert created["input"] == {"task": "schedule", "payload": {"schedule_id": result["id"]}}
    assert created["config"] is None
    assert created["metadata"] == {"kind": "schedule", "key": result["id"]}


async def test_create_admin_schedule_requires_admin_session(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    body = ScheduleCreateBody(
        name="Admin cleanup",
        prompt="Clean up workspace environments",
        schedule="0 9 * * *",
        admin_thread=True,
    )

    with pytest.raises(HTTPException) as exc:
        await schedules.create_agent_schedule("alice", body, email="alice@example.com")

    assert exc.value.status_code == 403
    assert fake_client.crons.created == []


async def test_create_admin_schedule_persists_admin_intent(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    body = ScheduleCreateBody(
        name="Admin cleanup",
        prompt="Clean up workspace environments",
        schedule="0 9 * * *",
        admin_thread=True,
    )

    result = await schedules.create_agent_schedule(
        "alice",
        body,
        email="alice@example.com",
        allow_admin_thread=True,
    )

    assert result["adminThread"] is True
    stored = fake_client.store.items[(tuple(schedules.SCHEDULES_NAMESPACE), result["id"])]
    assert stored["admin_thread"] is True


async def test_create_agent_schedule_requires_dashboard_token(fake_client, monkeypatch) -> None:  # noqa: ANN001
    async def no_token(login: str) -> None:
        return None

    monkeypatch.setattr(schedules, "get_valid_access_token", no_token)

    with pytest.raises(HTTPException) as exc:
        await schedules.create_agent_schedule(
            "alice", ScheduleCreateBody(prompt="hello", schedule="0 9 * * 1")
        )

    assert exc.value.status_code == 401
    assert fake_client.crons.created == []


async def test_create_agent_schedule_requires_repo_access(fake_client, auth, monkeypatch) -> None:  # noqa: ANN001, ARG001
    async def deny_repo(login: str, full_name: str | None) -> dict[str, str] | None:
        raise HTTPException(403, "no access to this private repository")

    monkeypatch.setattr(schedules, "repo_config_for_user", deny_repo)

    with pytest.raises(HTTPException) as exc:
        await schedules.create_agent_schedule(
            "alice",
            ScheduleCreateBody(
                prompt="hello",
                schedule="0 9 * * 1",
                repo="victim/private",
            ),
        )

    assert exc.value.status_code == 403
    assert fake_client.crons.created == []


async def test_list_agent_schedules_uses_owner_filters_and_paginates(fake_client) -> None:  # noqa: ANN001
    for i in range(125):
        await fake_client.store.put_item(
            agent_schedules.SCHEDULES_NAMESPACE,
            f"alice_{i}",
            {
                "id": f"alice_{i}",
                "name": f"Alice {i}",
                "prompt": "Run daily",
                "schedule": "0 9 * * *",
                "repo": None,
                "model": "Default",
                "enabled": True,
                "created_by": "alice",
                "user_email": "alice@example.com",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": f"2026-01-01T00:{i % 60:02d}:00+00:00",
            },
        )
    await fake_client.store.put_item(
        agent_schedules.SCHEDULE_RUN_STATE_NAMESPACE,
        "alice_0",
        {
            "schedule_id": "alice_0",
            "created_by": "alice",
            "user_email": "alice@example.com",
            "last_triggered_at": "2026-01-02T00:00:00+00:00",
        },
    )
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "bob_1",
        {
            "id": "bob_1",
            "name": "Bob",
            "prompt": "Run daily",
            "schedule": "0 9 * * *",
            "repo": None,
            "model": "Default",
            "enabled": True,
            "created_by": "bob",
            "user_email": "bob@example.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    result = await schedules.list_agent_schedules("alice", email="alice@example.com")

    assert len(result) == 125
    assert {item["id"] for item in result} == {f"alice_{i}" for i in range(125)}
    assert all(item["slackNotificationMode"] == "always" for item in result)
    assert all(item["adminThread"] is False for item in result)
    alice_zero = next(item for item in result if item["id"] == "alice_0")
    assert alice_zero["lastTriggeredAt"] == "2026-01-02T00:00:00+00:00"


async def test_update_agent_schedule_rechecks_repo_access(fake_client, auth, monkeypatch) -> None:  # noqa: ANN001, ARG001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )

    async def repo_config(login: str, full_name: str | None) -> dict[str, str] | None:
        assert full_name == "langchain-ai/open-swe"
        return {"owner": "langchain-ai", "name": "open-swe"}

    monkeypatch.setattr(schedules, "repo_config_for_user", repo_config)

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(repo="langchain-ai/open-swe"),
        email="alice@example.com",
    )

    assert result["repo"] == "langchain-ai/open-swe"
    assert fake_client.crons.created == []
    assert fake_client.crons.deleted == []


async def test_update_agent_schedule_clears_slack_channel(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "sched_1",
        _stored_schedule(slack_channel_id="C0123456789"),
    )

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(slack_channel_id=None),
        email="alice@example.com",
    )

    assert result["slackChannelId"] is None


async def test_update_agent_schedule_changes_slack_notification_mode(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "sched_1",
        _stored_schedule(slack_channel_id="C0123456789"),
    )

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(slack_notification_mode="on_action"),
        email="alice@example.com",
    )

    assert result["slackNotificationMode"] == "on_action"
    stored = fake_client.store.items[(tuple(agent_schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["slack_notification_mode"] == "on_action"


async def test_update_agent_schedule_rejects_non_admin_elevation(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule(admin_thread=False)
    )

    with pytest.raises(HTTPException) as exc:
        await schedules.update_agent_schedule(
            "sched_1",
            "alice",
            ScheduleUpdateBody(admin_thread=True),
            email="alice@example.com",
        )

    assert exc.value.status_code == 403
    stored = fake_client.store.items[(tuple(agent_schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["admin_thread"] is False


async def test_update_agent_schedule_allows_admin_elevation(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule(admin_thread=False)
    )
    _existing_cron(fake_client)

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(admin_thread=True),
        email="alice@example.com",
        allow_admin_thread=True,
    )

    assert result["adminThread"] is True
    stored = fake_client.store.items[(tuple(agent_schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["admin_thread"] is True


async def test_update_agent_schedule_replaces_cron_when_expression_changes(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )
    _existing_cron(fake_client)

    result = await schedules.update_agent_schedule(
        "sched_1",
        "alice",
        ScheduleUpdateBody(schedule="0 10 * * *"),
        email="alice@example.com",
    )

    assert fake_client.crons.deleted == ["cron_old"]
    assert result["cronId"] == "cron_1"
    assert fake_client.crons.created[0]["schedule"] == "0 10 * * *"


async def test_update_agent_schedule_pause_deletes_cron(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )
    _existing_cron(fake_client)

    result = await schedules.update_agent_schedule(
        "sched_1", "alice", ScheduleUpdateBody(enabled=False), email="alice@example.com"
    )

    assert result["enabled"] is False
    assert result["cronId"] is None
    assert fake_client.crons.deleted == ["cron_old"]
    assert fake_client.crons.created == []


async def test_delete_agent_schedule_removes_record_run_state_and_cron(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )
    await fake_client.store.put_item(
        agent_schedules.SCHEDULE_RUN_STATE_NAMESPACE, "sched_1", {"schedule_id": "sched_1"}
    )
    _existing_cron(fake_client)

    await schedules.delete_agent_schedule("sched_1", "alice", email="alice@example.com")

    assert fake_client.crons.deleted == ["cron_old"]
    assert fake_client.store.deleted == [
        (tuple(agent_schedules.SCHEDULES_NAMESPACE), "sched_1"),
        (tuple(agent_schedules.SCHEDULE_RUN_STATE_NAMESPACE), "sched_1"),
    ]


async def test_delete_agent_schedule_removes_a_pre_migration_cron(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )
    fake_client.crons.crons.append(
        {
            "cron_id": "cron_old",
            "metadata": {"kind": "agent_schedule", "schedule_id": "sched_1"},
        }
    )

    await schedules.delete_agent_schedule("sched_1", "alice", email="alice@example.com")

    assert fake_client.crons.deleted == ["cron_old"]


async def test_pause_removes_a_cron_known_only_from_the_record(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )

    result = await schedules.update_agent_schedule(
        "sched_1", "alice", ScheduleUpdateBody(enabled=False), email="alice@example.com"
    )

    assert result["cronId"] is None
    assert fake_client.crons.deleted == ["cron_old"]


async def test_trigger_agent_schedule_runs_paused_automation_as_test(
    fake_client, monkeypatch
) -> None:  # noqa: ANN001
    record = _stored_schedule(
        name="Weekly dependencies",
        prompt="Check dependencies",
        schedule="0 9 * * 1",
        enabled=False,
        cron_id=None,
        base_branch="main",
        branch_prefix="open-swe",
    )
    await fake_client.store.put_item(agent_schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    async def create_run(thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, str]:
        fake_client.runs.created.append(
            {"thread_id": thread_id, "assistant_id": assistant_id, **kwargs}
        )
        await fake_client.store.put_item(
            agent_schedules.SCHEDULES_NAMESPACE,
            "sched_1",
            {**record, "enabled": True, "cron_id": "cron_new"},
        )
        return {"run_id": "run_123"}

    monkeypatch.setattr(agent_schedules, "create_durable_run", create_run)

    result = await schedules.trigger_agent_schedule("sched_1", "alice", email="alice@example.com")

    assert result["status"] == "started"
    metadata = fake_client.threads.created[0]["metadata"]
    assert metadata["title"] == "Test: Weekly dependencies"
    assert metadata["schedule_test"] is True
    run = fake_client.runs.created[0]
    assert run["config"]["configurable"]["schedule_test"] is True
    stored = fake_client.store.items[(tuple(agent_schedules.SCHEDULES_NAMESPACE), "sched_1")]
    assert stored["enabled"] is True
    assert stored["cron_id"] == "cron_new"


async def test_trigger_agent_schedule_hides_unowned_automation(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _stored_schedule()
    )

    with pytest.raises(HTTPException) as exc:
        await schedules.trigger_agent_schedule("sched_1", "bob", email="bob@example.com")

    assert exc.value.status_code == 404
    assert fake_client.runs.created == []


async def test_trigger_agent_schedule_preserves_repo_auth_error(fake_client, monkeypatch) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "sched_1",
        _stored_schedule(repo={"owner": "langchain-ai", "name": "open-swe"}),
    )

    async def expired_token(login: str, full_name: str) -> str:
        raise HTTPException(401, "github token unavailable, re-login required")

    monkeypatch.setattr(agent_schedules, "require_repo_access_for_user", expired_token)

    with pytest.raises(HTTPException) as exc:
        await schedules.trigger_agent_schedule("sched_1", "alice", email="alice@example.com")

    assert exc.value.status_code == 401
    assert exc.value.detail == "github token unavailable, re-login required"
    assert fake_client.runs.created == []
