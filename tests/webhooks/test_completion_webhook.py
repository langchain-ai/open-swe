from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe import completion
from openswe.message_queue import QueuedMessage
from openswe.slack import thinking as slack_thinking
from openswe.tasks import events, store
from openswe.threads import runs
from tests.conftest import FakeStore


class _FakeThreads:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata
        self.updates: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self._metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append(metadata)


class _FakeClient:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.threads = _FakeThreads(metadata)


def _slack_metadata() -> dict[str, Any]:
    return {
        "source": "slack",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "123.45"}},
    }


@pytest.mark.asyncio
async def test_reviewer_error_preserves_pending_check_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "kind": "reviewer",
        "review_check_run_id": 42,
        "review_check_pending_result": {
            "conclusion": "success",
            "title": "Found 1 potential issue",
            "summary": "Open SWE surfaced 1 potential issue.",
        },
        "pr": {"owner": "acme", "name": "widgets"},
        "source": "schedule",
    }
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        completion, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    settle = AsyncMock()
    monkeypatch.setattr(completion, "settle_review_check_run", settle)

    await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    settle.assert_awaited_once_with(
        thread_id="t1",
        owner="acme",
        repo="widgets",
        token="token",
        conclusion="success",
        title="Found 1 potential issue",
        summary="Open SWE surfaced 1 potential issue.",
    )


@pytest.mark.asyncio
async def test_reviewer_cleanup_failure_does_not_block_failure_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _slack_metadata() | {
        "kind": "reviewer",
        "review_check_run_id": 42,
        "pr": {"owner": "acme", "name": "widgets"},
    }
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        completion, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        completion, "settle_review_check_run", AsyncMock(side_effect=RuntimeError("boom"))
    )
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    assert result["status"] == "ok"
    reply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "metadata", "worker", "cancelled", "picks_up"),
    [
        ("success", {}, False, False, True),
        ("error", {}, False, False, False),
        ("success", {"kind": "follow_up_pickup"}, False, False, False),
        ("success", {}, True, False, True),
        ("success", {}, True, True, False),
    ],
)
async def test_leftover_follow_ups_get_one_pickup_run(
    monkeypatch: pytest.MonkeyPatch,
    fake_store: FakeStore,
    registry_db: None,
    status: str,
    metadata: dict[str, object],
    worker: bool,
    cancelled: bool,
    picks_up: bool,
) -> None:
    pending = {"text": "Check logout too", "source": "dashboard"}
    await QueuedMessage.put("t1", pending)
    client = SimpleNamespace(
        threads=_FakeThreads({"source": "dashboard", "owner_login": "owner"}),
        runs=SimpleNamespace(list=AsyncMock(return_value=[])),
        store=fake_store,
    )
    dispatched: list[dict[str, object]] = []

    async def dispatch(
        thread_id: str, content: object, configurable: dict[str, object], **kwargs: object
    ) -> dict[str, str]:
        dispatched.append(
            {
                "thread_id": thread_id,
                "input": kwargs["input"],
                "metadata": kwargs["metadata"],
                "strategy": kwargs["multitask_strategy"],
            }
        )
        return {"run_id": "pickup-run"}

    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(completion, "schedule_answer_feedback", AsyncMock())
    monkeypatch.setattr(events, "worker_finished", AsyncMock(return_value=worker))
    monkeypatch.setattr(
        store.TaskDelegation, "get", AsyncMock(return_value=SimpleNamespace(cancelled=cancelled))
    )
    monkeypatch.setattr(
        runs, "_build_web_configurable", AsyncMock(return_value={"github_login": "owner"})
    )
    monkeypatch.setattr(runs, "dispatch_agent_run", dispatch)

    await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": status, "metadata": metadata}
    )

    assert dispatched == (
        [
            {
                "thread_id": "t1",
                "input": {"messages": []},
                "metadata": {"kind": completion.FOLLOW_UP_PICKUP_KIND},
                "strategy": "reject",
            }
        ]
        if picks_up
        else []
    )
    assert [message.content for message in await QueuedMessage.for_thread("t1")] == [pending]


@pytest.mark.asyncio
async def test_success_status_deduplicates_cost_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _slack_metadata()
    metadata["session_cost_refresh_scheduled_run_ids"] = ["run-1"]
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(completion, "schedule_answer_feedback", AsyncMock())
    schedule = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "schedule_session_cost_refresh", schedule)

    result = await completion.handle_run_completion(
        {
            "thread_id": "t1",
            "run_id": "run-1",
            "status": "success",
            "metadata": {"prepare_run_id": "prepare-1"},
        }
    )

    assert result["status"] == "ignored"
    schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_failed_run_posts_even_if_prior_run_replied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _slack_metadata()
    metadata["failure_reply_posted_run_ids"] = ["run-1"]
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-2", "status": "timeout"}
    )

    assert result["status"] == "ok"
    reply.assert_awaited_once()
    assert client.threads.updates == [
        {
            "failure_reply_posted_run_id": "run-2",
            "failure_reply_posted_run_ids": ["run-1", "run-2"],
        }
    ]


class _FakeRuns:
    def __init__(self, active: bool) -> None:
        self._active = active

    async def list(self, thread_id: str, status: str, limit: int) -> list[dict[str, Any]]:
        return [{"run_id": "run-2"}] if self._active else []


class _FakeActiveRunClient(_FakeClient):
    def __init__(self, metadata: dict[str, Any], active: bool) -> None:
        super().__init__(metadata)
        self.runs = _FakeRuns(active)


def _slack_metadata() -> dict[str, Any]:
    return {
        "source": "slack",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "123.45"}},
    }


def test_verify_run_complete_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # No secret configured: fail closed (reject everything).
    monkeypatch.setattr(completion, "RUN_COMPLETE_WEBHOOK_SECRET", None)
    assert completion.verify_run_complete_token(None) is False
    assert completion.verify_run_complete_token("whatever") is False

    # Secret configured: require an exact match.
    monkeypatch.setattr(completion, "RUN_COMPLETE_WEBHOOK_SECRET", "s3cret")
    assert completion.verify_run_complete_token("s3cret") is True
    assert completion.verify_run_complete_token("wrong") is False
    assert completion.verify_run_complete_token(None) is False


@pytest.mark.parametrize("status", ["success", "error"])
async def test_completion_waits_for_running_background_tasks(monkeypatch, status: str) -> None:
    metadata = {**_slack_metadata(), "running_background_tasks": ["cmd-1"]}
    client = _FakeActiveRunClient(metadata, active=False)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(completion, "post_slack_thread_reply", AsyncMock(return_value=True))
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await completion.handle_run_completion({"thread_id": "t1", "run_id": "run-1", "status": status})
    assert set_status.await_args.args == ("C1", "123.45", "Waiting for background tasks…")
