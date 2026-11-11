from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe import completion, session_cost
from openswe.message_queue import QueuedMessage
from openswe.review import run_failure
from openswe.slack import thinking as slack_thinking
from openswe.tasks import events, store
from openswe.threads import runs
from tests.conftest import MemoryEventClaims, serve_runs_from

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _failure_reply_claims(memory_event_claims: MemoryEventClaims) -> MemoryEventClaims:
    return memory_event_claims


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


@pytest.mark.parametrize("status", ["success", "error", "timeout", "interrupted"])
async def test_concierge_receipt_is_cleared_when_its_run_ends(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    client = _FakeClient({})
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "remove_slack_reaction", remove)

    await completion.handle_run_completion(
        {
            "thread_id": "t1",
            "run_id": "run-1",
            "status": status,
            "metadata": {
                "slack_channel_id": "D1",
                "slack_concierge_message_ts": "123.45",
            },
        }
    )

    remove.assert_awaited_once_with("D1", "123.45", "hourglass_flowing_sand")


@pytest.mark.parametrize("is_fallback", [False, True])
async def test_failed_run_offers_a_requester_scoped_retry(
    monkeypatch: pytest.MonkeyPatch, is_fallback: bool
) -> None:
    import json

    from openswe.slack.retry import SlackRetry

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "retry-test-secret-at-least-32-bytes")
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)
    error_link = completion.LangSmithErrorLink("https://smith.example/trace", is_fallback)
    monkeypatch.setattr(completion, "get_langsmith_error_url", AsyncMock(return_value=error_link))
    metadata = _slack_metadata()
    metadata["source_context"]["slack_thread"]["triggering_user_id"] = "U1"
    await completion._post_failure_reply("t1", metadata, "error", invocation_id="run-1")
    assert reply.await_args is not None
    text = reply.await_args.args[2]
    assert ("Couldn't link to the specific error trace" in text) is is_fallback
    assert f"<{error_link.url}|LangSmith>" in text
    blocks = reply.await_args.kwargs["blocks"]
    assert blocks[0]["text"]["text"] == text
    notice = reply.await_args.kwargs["notice"]
    assert blocks[0]["block_id"] == notice.block_id
    assert notice.repeats({"blocks": blocks})
    retry = SlackRetry.from_value(json.loads(blocks[1]["elements"][0]["value"]))
    assert retry is not None
    assert retry.model_copy(update={"expires_at": 0}) == SlackRetry(
        channel_id="C1", thread_ts="123.45", user_id="U1", nonce="run-1"
    )


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
        run_failure, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    settle = AsyncMock()
    monkeypatch.setattr(run_failure, "settle_review_check_run", settle)

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
        run_failure, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        run_failure, "settle_review_check_run", AsyncMock(side_effect=RuntimeError("boom"))
    )
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error"}
    )

    assert result["status"] == "ok"
    reply.assert_awaited_once()


class _FakePull:
    def __init__(self) -> None:
        self.comments: list[str] = []
        self.edits: list[tuple[int, str]] = []

    @asynccontextmanager
    async def as_app(self, owner: str, repo: str, number: int) -> AsyncIterator[_FakePull]:
        yield self

    async def comment(self, body: str) -> dict[str, int]:
        self.comments.append(body)
        return {"id": 900 + len(self.comments)}

    async def edit_comment(self, comment_id: int, body: str) -> dict[str, int]:
        self.edits.append((comment_id, body))
        return {"id": comment_id}


class _MergingThreads(_FakeThreads):
    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        await super().update(thread_id=thread_id, metadata=metadata)
        self._metadata.update(metadata)


@pytest.mark.asyncio
async def test_interrupted_reviewer_leaves_one_status_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata: dict[str, Any] = {
        "kind": "reviewer",
        "review_check_run_id": 42,
        "head_sha": "abc",
        "pr": {"owner": "acme", "name": "widgets", "number": 7},
    }
    client = _FakeClient(metadata)
    client.threads = _MergingThreads(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)

    async def set_metadata(thread_id: str, *, extra: dict[str, Any]) -> None:
        metadata.update(extra)

    async def settle(**kwargs: object) -> None:
        metadata["review_check_run_id"] = None

    settled = AsyncMock(side_effect=settle)
    monkeypatch.setattr(run_failure, "set_reviewer_thread_metadata", set_metadata)
    monkeypatch.setattr(run_failure, "settle_review_check_run", settled)
    monkeypatch.setattr(
        run_failure, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    pull = _FakePull()
    monkeypatch.setattr(run_failure.PullRequestClient, "as_app", pull.as_app)

    def failed(run_id: str) -> dict[str, Any]:
        return {
            "thread_id": "t1",
            "run_id": run_id,
            "status": "error",
            "error": {"error": "BackendTimeoutError", "message": "prepare failed (ReadTimeout)"},
        }

    first = await completion.handle_run_completion(failed("run-1"))
    replay = await completion.handle_run_completion(failed("run-1"))
    metadata["current_reviewer_run_id"] = "run-2"
    await completion.handle_run_completion(failed("run-2"))

    assert first["status"] == "ok"
    assert replay["status"] == "ignored"
    assert settled.await_args is not None
    assert settled.await_args.kwargs["conclusion"] == "neutral"
    assert len(pull.comments) == 1
    assert "backend was interrupted" in pull.comments[0]
    assert pull.edits == [(901, pull.comments[0])]
    # publish_review deletes the tracked status comment once a later run succeeds.
    assert metadata["status_comment_id"] == 901


@pytest.mark.asyncio
async def test_a_github_channel_run_still_reports_its_failure_on_the_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "github",
        "repo": {"owner": "acme", "name": "api"},
        "source_context": {"github_issue": {"id": "101", "number": 7, "title": "Bug"}},
    }
    monkeypatch.setattr(completion, "langgraph_client", lambda: _FakeClient(metadata))
    monkeypatch.setattr(
        completion, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    comment = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_github_comment", comment)

    # MDA's GitHub channel posts no failure notice, so Open SWE's reply stays.
    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error", "metadata": {"channel": "github"}}
    )

    assert result["status"] == "ok"
    repo, number, _text = comment.await_args.args
    assert (repo, number) == ({"owner": "acme", "name": "api"}, 7)


@pytest.mark.asyncio
async def test_a_channel_run_leaves_its_failure_to_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "linear",
        "source_context": {"linear_issue": {"id": "issue-1", "title": "Bug"}},
    }
    monkeypatch.setattr(completion, "langgraph_client", lambda: _FakeClient(metadata))
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "_post_failure_reply", notify)
    task_messages, wakes = AsyncMock(), AsyncMock()
    monkeypatch.setattr(completion.TaskMessage, "deliver_to", task_messages)
    monkeypatch.setattr(completion.EventSubscription, "deliver_to", wakes)

    # MDA's Linear channel started this run; it reports the failure in the session.
    result = await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-1", "status": "error", "metadata": {"channel": "linear"}}
    )
    assert result == {"status": "ignored", "reason": "the channel reports the failure"}
    notify.assert_not_awaited()
    # What arrived for the thread during the run doesn't wait for some later run.
    task_messages.assert_awaited_once_with("t1")
    wakes.assert_awaited_once_with("t1", "enqueue")

    # A run the backend started on the same thread still goes to its failure reply.
    await completion.handle_run_completion(
        {"thread_id": "t1", "run_id": "run-2", "status": "error"}
    )
    notify.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "metadata", "worker", "cancelled", "picks_up"),
    [
        ("success", {}, False, False, True),
        ("error", {}, False, False, False),
        ("success", {"kind": "follow_up_pickup"}, False, False, False),
        ("success", {}, True, False, True),
        ("success", {}, True, True, False),
        # The backend starts the pickup on an MDA channel's thread through its ingress.
        ("success", {"channel": "linear"}, False, False, True),
        ("success", {"channel": "linear", "kind": "follow_up_pickup"}, False, False, False),
    ],
)
async def test_leftover_follow_ups_get_one_pickup_run(
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(events, "worker_finished", AsyncMock(return_value=worker))
    monkeypatch.setattr(
        store.TaskDelegation, "get", AsyncMock(return_value=SimpleNamespace(cancelled=cancelled))
    )
    monkeypatch.setattr(
        runs, "_build_dashboard_configurable", AsyncMock(return_value={"github_login": "owner"})
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
async def test_success_status_settles_without_scheduling_cost_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(_slack_metadata())
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    schedule = AsyncMock(return_value=True)
    monkeypatch.setattr(session_cost, "schedule_session_cost_refresh", schedule)

    result = await completion.handle_run_completion(
        {
            "thread_id": "t1",
            "run_id": "run-1",
            "status": "success",
            "metadata": {"invocation_id": "invocation-1"},
        }
    )

    # Slack shows no run cost, so a settled run schedules nothing to refresh it.
    assert result == {"status": "ok", "reason": "run settled"}
    schedule.assert_not_awaited()
    assert not any("session_cost" in key for update in client.threads.updates for key in update)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type", "error_message"),
    [
        ("timeout", "TimeoutError", "model timed out"),
        ("error", "BackendUnavailableError", "after_model failed (HTTPStatusError)"),
    ],
)
async def test_later_failed_run_posts_even_if_prior_run_replied(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    error_type: str,
    error_message: str,
) -> None:
    metadata = _slack_metadata()
    metadata["failure_reply_posted_run_ids"] = ["run-1"]
    client = _FakeClient(metadata)
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)

    error_url = "https://smith.example/o/tenant/projects/p/project/r/failed-span"
    trace = AsyncMock(return_value=completion.LangSmithErrorLink(error_url, is_fallback=False))
    monkeypatch.setattr(completion, "get_langsmith_error_url", trace)

    result = await completion.handle_run_completion(
        {
            "thread_id": "t1",
            "run_id": "run-2",
            "status": status,
            "error": {"error": error_type, "message": error_message},
            "metadata": {"invocation_id": "invocation-2"},
        }
    )

    assert result["status"] == "ok"
    trace.assert_awaited_once_with("t1", "invocation-2", error_message)
    reply.assert_awaited_once()
    assert reply.await_args is not None
    assert reply.await_args.args[:2] == ("C1", "123.45")
    text = reply.await_args.args[2]
    assert f"<{error_url}|LangSmith>" in text
    if error_type == "BackendUnavailableError":
        assert "backend was interrupted" in text
        assert "Re-trigger the run" in text
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
    serve_runs_from(monkeypatch, client)
    monkeypatch.setattr(completion, "post_slack_thread_reply", AsyncMock(return_value=True))
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await completion.handle_run_completion({"thread_id": "t1", "run_id": "run-1", "status": status})
    assert set_status.await_args.args == ("C1", "123.45", "Waiting for background tasks…")
