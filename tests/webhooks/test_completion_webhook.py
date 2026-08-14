from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import completion


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
async def test_error_status_posts_slack_failure_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_slack_metadata())
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)
    monkeypatch.setattr(
        completion, "dashboard_thread_url", lambda thread_id: f"https://ui/{thread_id}"
    )

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    assert result["status"] == "ok"
    reply.assert_awaited_once()
    await_args = reply.await_args
    assert await_args is not None
    args = await_args.args
    assert args[0] == "C1"
    assert args[1] == "123.45"
    assert "<https://ui/t1|Open SWE Web>" in args[2]
    assert client.threads.updates == [
        {"failure_reply_posted_run_id": "run-1", "failure_reply_posted_run_ids": ["run-1"]}
    ]


@pytest.mark.asyncio
async def test_reviewer_error_settles_tracked_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEW_CHECK_BLOCKING", raising=False)
    metadata = {
        "kind": "reviewer",
        "review_check_run_id": 42,
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

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    assert result["status"] == "ignored"
    settle.assert_awaited_once_with(
        thread_id="t1",
        owner="acme",
        repo="widgets",
        token="token",
        conclusion="neutral",
        title="Review did not complete",
        summary=(
            "The Open SWE review run ended without publishing a review. "
            "Re-trigger the review by pushing a commit or re-requesting it."
        ),
    )


@pytest.mark.asyncio
async def test_reviewer_error_settles_failure_when_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVIEW_CHECK_BLOCKING", "true")
    metadata = {
        "kind": "reviewer",
        "review_check_run_id": 42,
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

    # A crashed reviewer must not satisfy a blocking (required) check.
    settle_args = settle.await_args
    assert settle_args is not None
    assert settle_args.kwargs["conclusion"] == "failure"


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
async def test_ordinary_agent_error_does_not_settle_review_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _slack_metadata()
    metadata["review_check_run_id"] = 42
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(completion, "post_slack_thread_reply", AsyncMock(return_value=True))
    token = AsyncMock(return_value="token")
    settle = AsyncMock()
    monkeypatch.setattr(completion, "get_github_app_installation_token", token)
    monkeypatch.setattr(completion, "settle_review_check_run", settle)

    await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    token.assert_not_awaited()
    settle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata, token",
    [
        ({"kind": "reviewer", "pr": {"owner": "acme", "name": "widgets"}}, "token"),
        ({"kind": "reviewer", "review_check_run_id": 42}, "token"),
        (
            {
                "kind": "reviewer",
                "review_check_run_id": 42,
                "pr": {"owner": "acme", "name": "widgets"},
            },
            None,
        ),
    ],
)
async def test_reviewer_cleanup_skips_missing_metadata_or_token(
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, Any],
    token: str | None,
) -> None:
    client = _FakeClient(metadata | {"source": "schedule"})
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(
        completion, "get_github_app_installation_token", AsyncMock(return_value=token)
    )
    settle = AsyncMock()
    monkeypatch.setattr(completion, "settle_review_check_run", settle)

    await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "timeout"}
    )

    settle.assert_not_awaited()


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
async def test_schedule_source_with_slack_context_posts_failure_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _slack_metadata()
    metadata["source"] = "schedule"
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    assert result["status"] == "ok"
    reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_status_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_slack_metadata())
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "success"}
    )

    assert result["status"] == "ignored"
    reply.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_when_already_replied(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = _slack_metadata()
    metadata["failure_reply_posted_run_ids"] = ["run-1"]
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "timeout"}
    )

    assert result["status"] == "ignored"
    reply.assert_not_called()
    assert client.threads.updates == []


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
async def test_linear_source_comments_on_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({"source": "linear", "source_context": {"linear_issue": {"id": "iss_1"}}})
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    comment = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "comment_on_linear_issue", comment)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "timeout"}
    )

    assert result["status"] == "ok"
    comment.assert_awaited_once()
    await_args = comment.await_args
    assert await_args is not None
    assert await_args.args[0] == "iss_1"


@pytest.mark.asyncio
async def test_missing_thread_id_is_ignored() -> None:
    result = await completion.handle_run_completion({"run_id": "run-1", "status": "error"})
    assert result["status"] == "ignored"


@pytest.mark.asyncio
async def test_missing_run_id_falls_back_to_thread_level_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(_slack_metadata())
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion({"thread_id": "t1", "status": "error"})

    assert result["status"] == "ok"
    reply.assert_awaited_once()
    assert client.threads.updates == [{"failure_reply_posted": True}]


@pytest.mark.asyncio
async def test_missing_run_id_respects_thread_level_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _slack_metadata()
    metadata["failure_reply_posted"] = True
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion({"thread_id": "t1", "status": "error"})

    assert result["status"] == "ignored"
    reply.assert_not_called()
    assert client.threads.updates == []


@pytest.mark.asyncio
async def test_no_reply_channel_does_not_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({"source": "schedule"})
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    assert result["status"] == "ignored"
    assert client.threads.updates == []


@pytest.mark.asyncio
async def test_interrupted_status_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # Follow-ups use multitask_strategy="interrupt", so an interrupted run is a
    # healthy hand-off, not a failure to report.
    client = _FakeClient(_slack_metadata())
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "interrupted"}
    )

    assert result["status"] == "ignored"
    reply.assert_not_called()
    assert client.threads.updates == []


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
