"""The shared cron ritual, the scheduler task registry, and launching a schedule."""

import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException
from support.langgraph_fakes import FakeLangGraphClient

from agent.graphs import scheduler
from agent.scheduling import agent_schedules, crons, tasks


@pytest.fixture
def fake_client(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
) -> FakeLangGraphClient:
    client = patched_langgraph_client(agent_schedules)
    client.runs.run_id = "run_123"
    client.crons.cron_id = "cron_{n}"
    return client


@pytest.fixture
def auth(monkeypatch) -> None:  # noqa: ANN001
    async def fake_require_repo_access_for_user(login: str, full_name: str) -> str:
        return "gho_token"

    async def fake_slack_id_for_login(login: str | None) -> str | None:
        return "UALICE" if login == "alice" else None

    monkeypatch.setattr(
        agent_schedules, "require_repo_access_for_user", fake_require_repo_access_for_user
    )
    monkeypatch.setattr(agent_schedules, "slack_id_for_login", fake_slack_id_for_login)


def _schedule_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "sched_1",
        "name": "Weekly dependencies",
        "prompt": "Check dependencies and open a PR if needed",
        "schedule": "0 9 * * 1",
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "model": "Default",
        "effort": None,
        "base_branch": "main",
        "branch_prefix": "open-swe",
        "enabled": True,
        "cron_id": "cron_1",
        "created_by": "alice",
        "user_email": "alice@example.com",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


async def test_ensure_scheduler_cron_creates_one_carrying_the_payload(fake_client) -> None:  # noqa: ANN001
    cron_id = await crons.ensure_scheduler_cron(
        fake_client,
        kind="baby_sit",
        key="acme/repo#7",
        schedule="*/10 * * * *",
        payload={"watch_key": "k"},
    )

    assert cron_id == "cron_1"
    created = fake_client.crons.created[0]
    assert created["assistant_id"] == "scheduler"
    assert created["schedule"] == "*/10 * * * *"
    assert created["timezone"] == "UTC"
    assert created["metadata"] == {"kind": "baby_sit", "key": "acme/repo#7"}
    assert created["input"] == {"task": "baby_sit", "payload": {"watch_key": "k"}}
    assert created["config"] is None


async def test_ensure_scheduler_cron_keeps_the_first_and_heals_duplicates(fake_client) -> None:  # noqa: ANN001
    metadata = {"kind": "background_tasks", "key": "thread-1"}
    fake_client.crons.crons.extend(
        [
            {"cron_id": "cron_a", "metadata": metadata},
            {"cron_id": "cron_b", "metadata": metadata},
            {"cron_id": "cron_c", "metadata": metadata},
        ]
    )

    cron_id = await crons.ensure_scheduler_cron(
        fake_client,
        kind="background_tasks",
        key="thread-1",
        schedule="* * * * *",
        payload={"thread_id": "thread-1"},
    )

    assert cron_id == "cron_a"
    assert fake_client.crons.created == []
    assert fake_client.crons.deleted == ["cron_b", "cron_c"]


async def test_ensure_scheduler_cron_retires_a_pre_migration_cron(fake_client) -> None:  # noqa: ANN001
    fake_client.crons.crons.append(
        {"cron_id": "cron_old", "metadata": {"kind": "baby_sit_watch", "watch_key": "acme/repo#7"}}
    )

    cron_id = await crons.ensure_scheduler_cron(
        fake_client,
        kind="baby_sit",
        key="acme/repo#7",
        schedule="*/10 * * * *",
        payload={"watch_key": "acme/repo#7"},
    )

    assert cron_id == "cron_1"
    assert fake_client.crons.deleted == ["cron_old"]


async def test_ensure_scheduler_cron_rejects_a_response_without_an_id(fake_client) -> None:  # noqa: ANN001
    fake_client.crons.cron_id = ""

    with pytest.raises(RuntimeError, match="did not return a cron_id"):
        await crons.ensure_scheduler_cron(
            fake_client,
            kind="schedule",
            key="sched_1",
            schedule="0 9 * * *",
            payload={"schedule_id": "sched_1"},
        )


async def test_delete_scheduler_crons_removes_every_match(fake_client) -> None:  # noqa: ANN001
    metadata = {"kind": "schedule", "key": "sched_1"}
    fake_client.crons.crons.extend(
        [
            {"cron_id": "cron_a", "metadata": metadata},
            {"cron_id": "cron_b", "metadata": metadata},
            {"cron_id": "other", "metadata": {"kind": "schedule", "key": "sched_2"}},
        ]
    )

    assert await crons.delete_scheduler_crons(fake_client, kind="schedule", key="sched_1") is True
    assert fake_client.crons.deleted == ["cron_a", "cron_b"]


async def test_delete_scheduler_crons_removes_pre_migration_crons(fake_client) -> None:  # noqa: ANN001
    fake_client.crons.crons.extend(
        [
            {
                "cron_id": "cron_old",
                "metadata": {"kind": "background_tasks", "thread_id": "thread-1"},
            },
            {"cron_id": "cron_new", "metadata": {"kind": "background_tasks", "key": "thread-1"}},
        ]
    )

    assert (
        await crons.delete_scheduler_crons(fake_client, kind="background_tasks", key="thread-1")
        is True
    )
    assert fake_client.crons.deleted == ["cron_new", "cron_old"]


async def test_delete_scheduler_crons_removes_the_recorded_cron_id(fake_client) -> None:  # noqa: ANN001
    assert (
        await crons.delete_scheduler_crons(
            fake_client, kind="schedule", key="sched_1", cron_id="cron_recorded"
        )
        is True
    )
    assert fake_client.crons.deleted == ["cron_recorded"]


async def test_delete_scheduler_crons_deletes_the_recorded_cron_once(fake_client) -> None:  # noqa: ANN001
    fake_client.crons.crons.append(
        {"cron_id": "cron_a", "metadata": {"kind": "schedule", "key": "sched_1"}}
    )

    assert (
        await crons.delete_scheduler_crons(
            fake_client, kind="schedule", key="sched_1", cron_id="cron_a"
        )
        is True
    )
    assert fake_client.crons.deleted == ["cron_a"]


async def test_delete_cron_reports_a_refused_delete(fake_client) -> None:  # noqa: ANN001
    async def refuse(cron_id: str) -> None:
        raise RuntimeError("platform said no")

    fake_client.crons.delete = refuse

    assert await crons.delete_cron(fake_client, "cron_a") is False


async def test_scheduler_graph_reads_the_payload_from_the_run_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor = AsyncMock(return_value={"status": "idle", "delivered": 0})
    monkeypatch.setattr(tasks, "monitor_background_tasks", monitor)

    state = await scheduler.get_scheduler().ainvoke(
        {"task": "background_tasks", "payload": {"thread_id": "thread-1"}}
    )

    assert state["result"] == {"status": "idle", "delivered": 0}
    monitor.assert_awaited_once_with("thread-1")


async def test_scheduler_task_ignores_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = AsyncMock(return_value={"status": "idle", "delivered": 0})
    monkeypatch.setattr(tasks, "monitor_background_tasks", monitor)

    state = await scheduler.get_scheduler().ainvoke(
        {"task": "background_tasks", "payload": {}},
        config={"configurable": {"thread_id": "thread-1"}},
    )

    assert state["result"] == {"status": "missing_thread_id"}
    monitor.assert_not_awaited()


async def test_scheduler_task_rejects_an_unregistered_task() -> None:
    assert await tasks.run_scheduler_task("nope", {}) == {"status": "unknown_task", "task": "nope"}
    assert await tasks.run_scheduler_task(None, {}) == {"status": "unknown_task", "task": None}


async def test_scheduler_task_reports_a_missing_schedule_id() -> None:
    assert await tasks.run_scheduler_task("schedule", {}) == {"status": "missing_schedule_id"}


async def test_scheduler_task_reports_a_missing_watch_key() -> None:
    assert await tasks.run_scheduler_task("baby_sit", {}) == {"status": "missing_watch_key"}


async def test_reconcile_task_needs_no_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sweep = AsyncMock(return_value={"threads_checked": 3, "stale_runs": 1, "cancelled": 1})
    monkeypatch.setattr(tasks, "reconcile_stale_runs", sweep)

    assert await tasks.run_scheduler_task("reconcile", {}) == {
        "threads_checked": 3,
        "stale_runs": 1,
        "cancelled": 1,
    }
    sweep.assert_awaited_once_with()


async def test_launch_scheduled_agent_run_reports_a_missing_schedule(fake_client) -> None:  # noqa: ANN001, ARG001
    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    assert result == {"status": "missing", "schedule_id": "sched_1"}


async def test_launch_scheduled_agent_run_skips_a_disabled_schedule(fake_client) -> None:  # noqa: ANN001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _schedule_record(enabled=False)
    )

    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    assert result == {"status": "disabled", "schedule_id": "sched_1"}
    assert fake_client.runs.created == []


async def test_launch_scheduled_agent_run_skips_when_repo_access_revoked(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE, "sched_1", _schedule_record()
    )

    async def deny_access(login: str, full_name: str) -> str:
        raise HTTPException(403, "no access to this private repository")

    monkeypatch.setattr(agent_schedules, "require_repo_access_for_user", deny_access)

    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    assert result == {
        "status": "unauthorized",
        "schedule_id": "sched_1",
        "error": "no access to this private repository",
        "status_code": 403,
    }
    assert fake_client.runs.created == []
    stored = fake_client.store.items[
        (tuple(agent_schedules.SCHEDULE_RUN_STATE_NAMESPACE), "sched_1")
    ]
    assert stored["last_error"] == "no access to this private repository"


async def test_launch_scheduled_agent_run_starts_fresh_agent_thread(fake_client, auth) -> None:  # noqa: ANN001, ARG001
    record = _schedule_record()
    await fake_client.store.put_item(agent_schedules.SCHEDULES_NAMESPACE, "sched_1", record)

    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    assert result["status"] == "started"
    thread_id = result["thread_id"]
    assert fake_client.threads.created[0]["thread_id"] == thread_id
    metadata = fake_client.threads.created[0]["metadata"]
    assert metadata["source"] == "schedule"
    assert metadata["origin"] == "schedule"
    assert metadata["thread_category"] == "automation"
    assert metadata["trigger_kind"] == "schedule"
    assert metadata["repo_owner"] == "langchain-ai"
    assert metadata["repo_name"] == "open-swe"
    run = fake_client.runs.created[0]
    assert run["thread_id"] == thread_id
    assert run["assistant_id"] == "agent"
    messages = run["input"]["messages"]
    assert ElementTree.fromstring(messages[0]["content"]).attrib["kind"] == "system"
    prompt = ElementTree.fromstring(messages[-1]["content"])
    assert prompt.findtext("content") == record["prompt"]
    assert run["durability"] == "sync"
    assert run["multitask_strategy"] == "interrupt"
    assert run["if_not_exists"] == "create"
    assert run["config"]["configurable"]["source"] == "schedule"
    assert run["config"]["configurable"]["repo"] == record["repo"]

    stored = fake_client.store.items[
        (tuple(agent_schedules.SCHEDULE_RUN_STATE_NAMESPACE), "sched_1")
    ]
    assert stored["last_thread_id"] == thread_id
    assert stored["last_run_id"] == "run_123"


async def test_launch_scheduled_agent_run_connects_slack_thread(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "sched_1",
        _schedule_record(
            name="Linear queue",
            prompt="Work the next Linear issue",
            schedule="*/15 * * * *",
            slack_channel_id="C0123456789",
        ),
    )
    posted: dict[str, Any] = {}

    async def fake_post(channel_id: str, text: str, **kwargs: Any) -> tuple[str, None]:
        posted.update({"channel_id": channel_id, "text": text, "kwargs": kwargs})
        return "1784302353.900029", None

    monkeypatch.setattr(agent_schedules, "post_slack_top_level_message_with_ts", fake_post)

    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    expected_thread_id = result["thread_id"]
    assert uuid.UUID(expected_thread_id).version == 4
    assert posted["channel_id"] == "C0123456789"
    assert "Linear queue" in posted["text"]
    metadata = fake_client.threads.created[0]["metadata"]
    slack_thread = metadata["source_context"]["slack_thread"]
    assert slack_thread["channel_id"] == "C0123456789"
    assert slack_thread["thread_ts"] == "1784302353.900029"
    assert slack_thread["triggering_user_id"] == "UALICE"
    run = fake_client.runs.created[0]
    assert run["config"]["configurable"]["slack_thread"] == slack_thread
    prompt = ElementTree.fromstring(run["input"]["messages"][-1]["content"])
    assert "slack_thread_reply" in (prompt.findtext("content") or "")
    association = fake_client.store.items[
        (("slack_thread_map", "C0123456789"), "1784302353.900029")
    ]
    assert association["thread_id"] == expected_thread_id
    mapping = fake_client.store.items[
        (("slack_run_map", "C0123456789"), "thread:1784302353.900029")
    ]
    assert mapping["run_id"] == "run_123"


async def test_launch_conditional_slack_schedule_starts_silently(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "sched_1",
        _schedule_record(
            name="Dependency check",
            prompt="Open a PR if dependencies need updates",
            slack_channel_id="C0123456789",
            slack_notification_mode="on_action",
        ),
    )

    async def fail_if_posted(*args: Any, **kwargs: Any) -> tuple[None, None]:
        raise AssertionError("conditional schedule should not post at launch")

    monkeypatch.setattr(agent_schedules, "post_slack_top_level_message_with_ts", fail_if_posted)

    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    assert result["status"] == "started"
    run = fake_client.runs.created[0]
    configurable = run["config"]["configurable"]
    assert "slack_thread" not in configurable
    assert configurable["automation_slack_notification"] == {
        "channel_id": "C0123456789",
        "mode": "on_action",
        "schedule_id": "sched_1",
        "schedule_name": "Dependency check",
    }
    prompt = ElementTree.fromstring(run["input"]["messages"][-1]["content"])
    assert "notify_automation_channel" in (prompt.findtext("content") or "")
    assert "read-only checks" in (prompt.findtext("content") or "")
    metadata = fake_client.threads.created[0]["metadata"]
    assert "source_context" not in metadata


async def test_launch_scheduled_agent_run_stops_when_slack_post_fails(
    fake_client, auth, monkeypatch
) -> None:  # noqa: ANN001, ARG001
    await fake_client.store.put_item(
        agent_schedules.SCHEDULES_NAMESPACE,
        "sched_1",
        _schedule_record(
            name="Linear queue",
            prompt="Work the next Linear issue",
            repo=None,
            slack_channel_id="C0123456789",
        ),
    )

    async def fake_post(*args: Any, **kwargs: Any) -> tuple[None, str]:
        return None, "not_in_channel"

    monkeypatch.setattr(agent_schedules, "post_slack_top_level_message_with_ts", fake_post)

    result = await agent_schedules.launch_scheduled_agent_run("sched_1")

    assert result == {
        "status": "error",
        "schedule_id": "sched_1",
        "error": "Slack post failed: not_in_channel",
    }
    assert fake_client.threads.created == []
    assert fake_client.runs.created == []
    stored = fake_client.store.items[
        (tuple(agent_schedules.SCHEDULE_RUN_STATE_NAMESPACE), "sched_1")
    ]
    assert stored["last_error"] == "Slack post failed: not_in_channel"
