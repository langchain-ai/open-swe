from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.github.comments import fetch_pr_comments_since_last_tag
from agent.slack import webhook
from agent.slack.request import SlackRequest
from agent.slack.tools import move_thread


@pytest.mark.asyncio
async def test_github_batch_ends_at_authorized_event(monkeypatch):
    from agent.github import comments

    event = {
        "body": "@open-swe Alice's request",
        "author": "alice",
        "created_at": "2026-01-01T00:00:00Z",
        "type": "pr_comment",
        "comment_id": 1,
    }
    monkeypatch.setattr(
        comments,
        "_fetch_paginated",
        AsyncMock(
            side_effect=[
                [
                    {
                        "id": 1,
                        "body": "edited after webhook",
                        "user": {"login": "alice"},
                        "created_at": event["created_at"],
                    },
                    {
                        "id": 2,
                        "body": "@open-swe Bob's request",
                        "user": {"login": "bob"},
                        "created_at": "2026-01-01T00:00:01Z",
                    },
                ],
                [],
                [],
            ]
        ),
    )
    result = await fetch_pr_comments_since_last_tag(
        {"owner": "org", "name": "repo"}, 1, token="test", event_comment=event
    )
    assert result == [event]


@pytest.mark.asyncio
async def test_review_batch_keeps_same_second_inline_and_ignores_non_owner_tags(monkeypatch):
    from agent.github import comments

    event = {
        "body": "@open-swe address review",
        "author": "alice",
        "type": "review",
        "comment_id": 90,
        "created_at": "2026-01-01T00:00:02Z",
    }
    monkeypatch.setattr(
        comments,
        "_fetch_paginated",
        AsyncMock(
            side_effect=[
                [
                    {
                        "id": 1,
                        "body": "Alice context",
                        "user": {"login": "alice"},
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "body": "@open-swe Bob",
                        "user": {"login": "bob"},
                        "created_at": "2026-01-01T00:00:01Z",
                    },
                ],
                [
                    {
                        "id": 3,
                        "body": "inline fix",
                        "user": {"login": "alice"},
                        "created_at": event["created_at"],
                        "pull_request_review_id": 90,
                    },
                    {
                        "id": 4,
                        "body": "other review",
                        "user": {"login": "alice"},
                        "created_at": event["created_at"],
                        "pull_request_review_id": 91,
                    },
                ],
                [],
            ]
        ),
    )
    result = await fetch_pr_comments_since_last_tag(
        {}, 1, token="test", event_comment=event, authorized_login="alice"
    )
    assert [c["body"] for c in result] == ["Alice context", "inline fix", event["body"]]


@pytest.mark.asyncio
async def test_edited_github_trigger_keeps_context_since_previous_tag(monkeypatch):
    from agent.github import comments

    event = {
        "body": "@open-swe revised request",
        "author": "alice",
        "type": "pr_comment",
        "comment_id": 1,
        "created_at": "2026-01-01T00:00:01Z",
        "event_at": "2026-01-01T00:00:05Z",
    }
    records = [
        {
            "id": index,
            "body": body,
            "user": {"login": "alice"},
            "created_at": f"2026-01-01T00:00:0{index}Z",
        }
        for index, body in [
            (1, "@open-swe stale trigger"),
            (2, "@open-swe previous invocation"),
            (3, "Do not delete customer data"),
            (6, "Future context"),
        ]
    ]
    monkeypatch.setattr(comments, "_fetch_paginated", AsyncMock(side_effect=[records, [], []]))
    result = await fetch_pr_comments_since_last_tag(
        {}, 1, token="test", event_comment=event, authorized_login="alice"
    )
    assert [c["body"] for c in result] == ["Do not delete customer data", event["body"]]
    assert result[-1] == event


@pytest.mark.asyncio
async def test_private_slack_edit_rejects_non_owner(monkeypatch):
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": "private", "owner_login": "alice"}}
            )
        )
    )
    monkeypatch.setattr(webhook, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(webhook.common, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(webhook.common, "refresh_user_mapping_cache", AsyncMock())
    monkeypatch.setattr(webhook.common, "get_slack_user_info", AsyncMock(return_value={}))
    monkeypatch.setattr(webhook, "_slack_login", AsyncMock(return_value="bob"))
    queue = AsyncMock()
    monkeypatch.setattr(webhook, "queue_message_for_thread", queue)
    request = SlackRequest(
        channel_id="C1",
        thread_ts="1",
        event_ts="2",
        user_id="BOB",
        text="use Alice's tools",
        thread_id="private-thread",
        message_update=True,
    )
    with pytest.raises(HTTPException) as exc:
        await webhook._process_slack_mention_impl(request, None)
    assert exc.value.status_code == 404
    queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_slack_move_cannot_publish_to_channel(monkeypatch):
    monkeypatch.setattr(
        move_thread,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "private-thread",
                "slack_thread": {"channel_id": "D1", "thread_ts": "1"},
            }
        },
    )
    monkeypatch.setattr(
        move_thread,
        "langgraph_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(
                    return_value={"metadata": {"visibility": "private", "owner_login": "alice"}}
                )
            )
        ),
    )
    post = AsyncMock()
    monkeypatch.setattr(move_thread, "post_slack_top_level_message_with_ts", post)
    result = await move_thread.slack_move_thread("Move here", "C1")
    assert result["success"] is False
    post.assert_not_awaited()
