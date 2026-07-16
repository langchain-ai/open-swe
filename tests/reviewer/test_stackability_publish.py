from __future__ import annotations

import importlib
from unittest.mock import AsyncMock

import pytest

from agent.review.findings import ReviewerThreadMissingError
from agent.review.stackability import new_stackability_review_record

publisher = importlib.import_module("agent.tools.publish_stackability_review")


def _artifact():
    return new_stackability_review_record(
        "head",
        {
            "verdict": "not_worth_splitting",
            "confidence": "high",
            "rationale": "The change is one atomic behavior with its tests.",
            "proposed_stack": [],
            "harness_prompt": "Keep the source branch intact and create new branches if needed.",
            "risks_or_human_decisions": [],
        },
    )


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(
        publisher,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "reviewer-1",
                "repo": {"owner": "acme", "name": "repo"},
                "pr_number": 7,
                "head_sha": "head",
            }
        },
    )
    monkeypatch.setattr(publisher, "get_github_token", lambda: "token")
    monkeypatch.setattr(publisher, "resolve_review_head_sha", AsyncMock(return_value="head"))


@pytest.mark.asyncio
async def test_publish_stackability_review_posts_and_persists(monkeypatch) -> None:
    _configure(monkeypatch)
    update = AsyncMock()
    post = AsyncMock(return_value=91)
    monkeypatch.setattr(publisher, "get_stackability_review", AsyncMock(return_value=_artifact()))
    monkeypatch.setattr(publisher, "post_status_comment", post)
    monkeypatch.setattr(publisher, "update_stackability_review", update)

    result = await publisher.publish_stackability_review()

    assert result == {"success": True, "github_comment_id": 91, "already_published": False}
    post_call = post.await_args
    update_call = update.await_args
    assert post_call is not None
    assert update_call is not None
    assert "<!-- open-swe-stackability-review -->" in post_call.kwargs["body"]
    publication = update_call.kwargs["publication"]
    assert publication["mode"] == "manual_advisory"
    assert publication["state"] == "published"
    assert publication["github_comment_id"] == 91


@pytest.mark.asyncio
async def test_publish_stackability_review_is_idempotent(monkeypatch) -> None:
    _configure(monkeypatch)
    artifact = _artifact()
    artifact["publication"].update(mode="manual_advisory", state="published", github_comment_id=91)
    post = AsyncMock()
    monkeypatch.setattr(publisher, "get_stackability_review", AsyncMock(return_value=artifact))
    monkeypatch.setattr(publisher, "post_status_comment", post)

    result = await publisher.publish_stackability_review()

    assert result == {"success": True, "github_comment_id": 91, "already_published": True}
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configurable", "token", "artifact", "error"),
    [
        ({}, "token", None, "reviewer_thread_unavailable"),
        ({"thread_id": "reviewer-1"}, "token", None, "missing_repo_config"),
        (
            {"thread_id": "reviewer-1", "repo": {"owner": "acme", "name": "repo"}},
            "token",
            None,
            "missing_pr_number",
        ),
        (
            {
                "thread_id": "reviewer-1",
                "repo": {"owner": "acme", "name": "repo"},
                "pr_number": 7,
            },
            None,
            None,
            "github_token_unavailable",
        ),
        (
            {
                "thread_id": "reviewer-1",
                "repo": {"owner": "acme", "name": "repo"},
                "pr_number": 7,
            },
            "token",
            None,
            "stackability_review_unavailable",
        ),
    ],
)
async def test_publish_stackability_review_structured_prerequisite_errors(
    monkeypatch, configurable, token, artifact, error
) -> None:
    monkeypatch.setattr(publisher, "get_config", lambda: {"configurable": configurable})
    monkeypatch.setattr(publisher, "get_github_token", lambda: token)
    monkeypatch.setattr(publisher, "get_stackability_review", AsyncMock(return_value=artifact))

    assert (await publisher.publish_stackability_review())["error"] == error


@pytest.mark.asyncio
async def test_publish_stackability_review_rejects_stale_head(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(publisher, "get_stackability_review", AsyncMock(return_value=_artifact()))
    monkeypatch.setattr(publisher, "resolve_review_head_sha", AsyncMock(return_value="new-head"))

    result = await publisher.publish_stackability_review()

    assert result["error"] == "stale_stackability_review"
    assert result["live_head_sha"] == "new-head"


@pytest.mark.asyncio
async def test_publish_stackability_review_handles_failed_post(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(publisher, "get_stackability_review", AsyncMock(return_value=_artifact()))
    monkeypatch.setattr(publisher, "post_status_comment", AsyncMock(return_value=None))

    assert (await publisher.publish_stackability_review())[
        "error"
    ] == "stackability_publication_failed"


@pytest.mark.asyncio
async def test_publish_stackability_review_handles_missing_thread(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(
        publisher,
        "get_stackability_review",
        AsyncMock(side_effect=ReviewerThreadMissingError("reviewer-1", RuntimeError("missing"))),
    )

    result = await publisher.publish_stackability_review()

    assert result["success"] is False
    assert result["error"] == "thread_not_found"
