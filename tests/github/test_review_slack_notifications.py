import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import chain, repeat
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.github import notifications, webhook
from agent.github.pull_requests import PullRequest
from agent.slack import thinking
from agent.users import User
from agent.webhooks import common


@pytest.fixture
def review_notice(monkeypatch: pytest.MonkeyPatch, fake_store) -> tuple[MagicMock, AsyncMock]:
    client = MagicMock()
    client.threads.get = AsyncMock(
        return_value={
            "metadata": {
                "source": "github",
                "source_context": {
                    "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.123456"}
                },
            }
        }
    )
    client.runs.list = AsyncMock(return_value=[])
    monkeypatch.setattr(thinking, "set_slack_thread_status", AsyncMock(return_value=True))
    lock = asyncio.Lock()

    @asynccontextmanager
    async def locked(*args: object, thread_id: str) -> AsyncIterator[dict[str, object] | None]:
        async with lock:
            yield await notifications.get_active_slack_thread(client, thread_id)

    post = AsyncMock(return_value=("1700000001.123456", None))
    monkeypatch.setattr(notifications, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(notifications, "slack_thread_mutation_lock", locked)
    monkeypatch.setattr(notifications, "post_slack_thread_reply_with_ts", post)
    return client, post


@pytest.mark.parametrize("body", ["", "Please fix this before merging."])
@pytest.mark.parametrize(
    ("state", "suffix"),
    [
        ("approved", ": *Approved*."),
        ("changes_requested", ": *Changes requested*."),
        ("commented", ": *Comments left*."),
        ("APPROVED", ": *Approved*."),
        ("unknown", "."),
        (None, "."),
    ],
)
async def test_review_wakeup_posts_one_linked_notice(
    monkeypatch: pytest.MonkeyPatch, review_notice, body: str, state: str | None, suffix: str
) -> None:
    client, post = review_notice
    monkeypatch.setattr(common, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(PullRequest, "link_thread", AsyncMock())
    monkeypatch.setattr(webhook.postgres, "configured", lambda: False)
    monkeypatch.setattr(User, "email_for_login", AsyncMock(return_value="octo@example.com"))
    monkeypatch.setattr(User, "known_logins", AsyncMock(return_value=frozenset({"octo"})))
    monkeypatch.setattr(common, "get_or_resolve_thread_github_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(common, "react_to_github_comment", AsyncMock())
    monkeypatch.setattr(
        common,
        "extract_pr_context",
        AsyncMock(
            return_value=(
                {"owner": "o", "name": "r"},
                7,
                "",
                "octo",
                "https://github.com/o/r/pull/7",
                42,
                None,
            )
        ),
    )
    comments = [
        {
            "body": body or "_Submitted a review: changes_requested_",
            "author": "octo",
            "type": "review",
        },
        {"body": "An inline finding", "author": "octo", "type": "review_comment"},
    ]
    fetch = AsyncMock(return_value=comments)
    monkeypatch.setattr(common, "fetch_pr_event_comments", fetch)
    dispatch = AsyncMock(return_value=True)
    monkeypatch.setattr(common, "trigger_or_queue_run", dispatch)
    payload = {
        "action": "submitted",
        "sender": {"login": "octo", "id": 1},
        "review": {
            "body": body,
            "user": {"login": "octo"},
            "submitted_at": "2026-01-01T00:00:00Z",
            **({"state": state} if state is not None else {}),
        },
    }

    async def deliver() -> None:
        await webhook.process_github_pr_comment(
            payload, "pull_request_review", agent_thread_id="agent-thread"
        )

    dispatch.return_value = False
    await deliver()
    post.assert_not_awaited()
    dispatch.return_value = True
    client.threads.get.return_value["metadata"].update(visibility="private", owner_login="alice")
    dispatch.reset_mock()
    await deliver()
    dispatch.assert_not_awaited()
    post.assert_not_awaited()
    client.threads.get.return_value["metadata"].pop("visibility")
    fetch.return_value = []
    await deliver()
    post.assert_not_awaited()
    fetch.return_value = comments
    await asyncio.gather(deliver(), deliver())
    post.assert_awaited_once_with(
        "C123",
        "1700000000.123456",
        f"@octo <https://github.com/o/r/pull/7#pullrequestreview-42|submitted a review on o/r#7>{suffix}",
        agent_thread_id="agent-thread",
        unfurl_links=False,
        unfurl_media=False,
    )
    assert dispatch.await_count == 2

    payload["action"] = "edited"
    for index, edited_body in enumerate(["A", "B", "A"], start=1):
        payload["review"].update(body=edited_body, updated_at=f"2026-01-01T00:00:0{index}Z")
        comments[0]["body"] = edited_body
        await asyncio.gather(deliver(), deliver())
        assert post.await_count == index + 1
        assert post.call_args.args[2] == (
            f"@octo <https://github.com/o/r/pull/7#pullrequestreview-42|edited a review on o/r#7>{suffix}"
        )


@pytest.mark.parametrize("run_status", ["pending", "running", "completed"])
async def test_review_notice_preserves_current_run_status(
    monkeypatch: pytest.MonkeyPatch,
    review_notice: tuple[MagicMock, AsyncMock],
    run_status: Literal["pending", "running", "completed"],
) -> None:
    client, post = review_notice
    slack_status = "Thinking..."

    async def set_status(
        channel_id: str,
        thread_ts: str,
        status: str,
        *,
        loading_messages: list[str] | None = None,
    ) -> bool:
        nonlocal slack_status
        assert (channel_id, thread_ts) == ("C123", "1700000000.123456")
        slack_status = status
        return True

    async def list_runs(thread_id: str, *, status: str, limit: int) -> list[dict[str, str]]:
        return [{"run_id": "run-1"}] if status == run_status else []

    async def post_notice(*args: object, **kwargs: object) -> tuple[str, None]:
        nonlocal slack_status
        slack_status = ""
        client.runs.list.side_effect = list_runs
        return "1700000001.123456", None

    client.runs.list.return_value = [{"run_id": "run-1"}]
    post.side_effect = post_notice
    monkeypatch.setattr(thinking, "set_slack_thread_status", set_status)

    await notifications.notify_slack_review(
        "agent-thread",
        reviewer="octo",
        pr_label="o/r#7",
        review_url="https://github.com/o/r/pull/7#pullrequestreview-42",
    )

    post.assert_awaited_once()
    assert slack_status == ("" if run_status == "completed" else "Thinking...")


@pytest.mark.parametrize("detached", [False, True])
async def test_reviews_do_not_create_slack_threads(review_notice, detached: bool) -> None:
    client, post = review_notice
    client.threads.get.return_value = {
        "metadata": {
            "source": "github",
            **({"slack_thread_detached_at": "now"} if detached else {}),
        }
    }
    await notifications.notify_slack_review(
        "agent-thread",
        reviewer="octo",
        pr_label="o/r#7",
        review_url="https://github.com/o/r/pull/7#pullrequestreview-42",
    )
    post.assert_not_awaited()


async def test_distinct_reviews_and_edits_follow_current_slack_location(review_notice) -> None:
    client, post = review_notice
    url = "https://github.com/o/r/pull/7#pullrequestreview-42"
    await notifications.notify_slack_review(
        "agent-thread", reviewer="octo", pr_label="o/r#7", review_url=url
    )
    client.threads.get.return_value["metadata"]["source_context"]["slack_thread"]["channel_id"] = (
        "C456"
    )
    for _ in range(2):
        await notifications.notify_slack_review(
            "agent-thread",
            reviewer="octo",
            pr_label="o/r#7",
            review_url=url,
            edited_body="Updated review",
        )
    assert post.await_count == 2
    assert post.call_args.args == (
        "C456",
        "1700000000.123456",
        f"@octo <{url}|edited a review on o/r#7>.",
    )
    await notifications.notify_slack_review(
        "agent-thread", reviewer="octo", pr_label="o/r#7", review_url=url + "1"
    )
    assert post.await_count == 3


@pytest.mark.parametrize("detached", [False, True])
async def test_review_rechecks_destination_after_concurrent_move(
    review_notice, detached: bool
) -> None:
    client, post = review_notice
    original = client.threads.get.return_value
    destination = {
        "metadata": {
            "source_context": {
                "slack_thread": {"channel_id": "C456", "thread_ts": "1700000002.123456"}
            }
        }
    }
    client.threads.get.side_effect = chain(
        [original, {"metadata": {}} if detached else destination], repeat(destination)
    )
    await notifications.notify_slack_review(
        "agent-thread",
        reviewer="octo",
        pr_label="o/r#7",
        review_url="https://github.com/o/r/pull/7#pullrequestreview-42",
    )
    if detached:
        post.assert_not_awaited()
    else:
        post.assert_awaited_once()
        assert post.call_args.args[:2] == ("C456", "1700000002.123456")


@pytest.mark.parametrize("raises", [False, True])
async def test_slack_failures_are_logged_without_retrying_uncertain_delivery(
    review_notice, caplog, raises: bool
) -> None:
    _, post = review_notice
    if raises:
        post.side_effect = TimeoutError("response lost")
    else:
        post.return_value = (None, "post_failed")
    for _ in range(2):
        await notifications.notify_slack_review(
            "agent-thread",
            reviewer="octo",
            pr_label="o/r#7",
            review_url="https://github.com/o/r/pull/7#pullrequestreview-42",
        )
    post.assert_awaited_once()
    assert "Failed to" in caplog.text
