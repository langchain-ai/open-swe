from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import completion
from agent.slack import thinking as slack_thinking


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
    ("status", "metadata", "picks_up"),
    [
        ("success", {}, True),
        # A run that failed before its first model call left the store as it
        # was; restarting it would only fail again, forever.
        ("error", {}, False),
        ("success", {"kind": "follow_up_pickup"}, False),
    ],
)
async def test_leftover_follow_ups_get_one_pickup_run(
    monkeypatch: pytest.MonkeyPatch, status: str, metadata: dict[str, Any], picks_up: bool
) -> None:
    monkeypatch.setattr(
        completion, "langgraph_client", lambda: _FakeClient({"source": "dashboard"})
    )
    monkeypatch.setattr(completion, "schedule_answer_feedback", AsyncMock())
    pickup = AsyncMock()
    monkeypatch.setattr(completion, "_start_run_for_pending_follow_ups", pickup)

    await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": status, "metadata": metadata}
    )

    assert pickup.await_count == (1 if picks_up else 0)


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


@pytest.mark.asyncio
async def test_repeated_event_woken_failures_stop_replying_but_person_started_runs_still_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(_slack_metadata() | {"consecutive_failed_runs": 3})
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    suppressed = await completion.handle_run_completion(
        {
            "thread_id": "t1",
            "run_id": "run-4",
            "status": "error",
            "metadata": {"kind": "event_match"},
        }
    )
    person_started = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-5", "status": "error"}
    )

    assert suppressed == {"status": "ignored", "reason": "repeated failures"}
    assert person_started["status"] == "ok"
    reply.assert_awaited_once()
    assert client.threads.updates[0] == {"consecutive_failed_runs": 4}
    assert client.threads.updates[1]["consecutive_failed_runs"] == 0


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
