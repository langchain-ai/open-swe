import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx2
import pytest
from fastapi import HTTPException

from openswe.dashboard import profiles
from openswe.github import http as github_http
from openswe.github.checks import github_headers
from openswe.github.http import GitHubError
from openswe.review import conversation
from openswe.review.conversation import (
    ConversationComment,
    ConversationCommentCreate,
    ConversationCommit,
    ConversationReview,
    api_get_review_conversation,
    api_post_review_conversation_comment,
)

Handler = Callable[[httpx2.Request], httpx2.Response]
SESSION = {"sub": "octocat"}


def _user(login: str) -> dict[str, str]:
    return {"login": login, "avatar_url": f"https://avatars.example/{login}"}


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx2.Request]]:
    async def allow(login: str, full_name: str) -> str:
        return "viewer-token"

    async def token(login: str) -> str:
        return "viewer-token"

    monkeypatch.setattr(conversation, "require_repo_access_for_user", allow)
    monkeypatch.setattr(profiles, "get_valid_access_token", token)

    def install(handler: Handler) -> list[httpx2.Request]:
        seen: list[httpx2.Request] = []

        def record(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return handler(request)

        @asynccontextmanager
        async def client(*, token: str, **_kwargs: object) -> AsyncIterator[httpx2.AsyncClient]:
            async with httpx2.AsyncClient(
                headers=github_headers(token), transport=httpx2.MockTransport(record)
            ) as http:
                yield http

        monkeypatch.setattr(github_http, "github_client", client)
        return seen

    return install


async def test_timeline_merges_sorts_and_drops_pending_reviews(
    github: Callable[[Handler], list[httpx2.Request]],
) -> None:
    full_page = [
        {
            "id": 1000 + i,
            "user": _user("bot"),
            "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
            "body": f"page one {i}",
            "html_url": f"https://github.com/acme/app/pull/7#issuecomment-{1000 + i}",
        }
        for i in range(100)
    ]
    late_comment = {
        "id": 2,
        "user": None,
        "created_at": "2026-01-03T00:00:00Z",
        "body": None,
        "html_url": "https://github.com/acme/app/pull/7#issuecomment-2",
    }
    reviews = [
        {
            "id": 10,
            "user": _user("alice"),
            "state": "APPROVED",
            "submitted_at": "2026-01-02T00:00:00Z",
            "body": "lgtm",
            "html_url": "https://github.com/acme/app/pull/7#pullrequestreview-10",
        },
        {
            "id": 11,
            "user": _user("bob"),
            "state": "PENDING",
            "body": "draft",
            "html_url": "https://github.com/acme/app/pull/7#pullrequestreview-11",
        },
        {
            "id": 12,
            "user": _user("carol"),
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2025-12-31T00:00:00Z",
            "body": "",
            "html_url": "https://github.com/acme/app/pull/7#pullrequestreview-12",
        },
    ]

    def inline_comment(
        comment_id: int, review_id: int, reply_to: int | None = None, line: int | None = 5
    ) -> dict[str, object]:
        return {
            "id": comment_id,
            "pull_request_review_id": review_id,
            "in_reply_to_id": reply_to,
            "user": _user("carol"),
            "created_at": f"2025-12-31T00:00:{comment_id:02d}Z",
            "body": f"note {comment_id} <!-- open-swe-review-comment {{}} -->",
            "html_url": f"https://github.com/acme/app/pull/7#discussion_r{comment_id}",
            "path": "src/app.py",
            "line": line,
            "side": "RIGHT",
        }

    inline = [
        inline_comment(21, 12),
        inline_comment(22, 12, reply_to=21),
        inline_comment(23, 10, line=None),
        inline_comment(24, 11),
    ]
    commits = [
        {
            "sha": "abc123",
            "html_url": "https://github.com/acme/app/commit/abc123",
            "commit": {"message": "Add app", "author": {"date": "2025-12-30T00:00:00Z"}},
            "author": {**_user("dave"), "type": "Bot"},
        }
    ]
    thread_states = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "PRRT_21",
                                "isResolved": True,
                                "isOutdated": False,
                                "comments": {"nodes": [{"fullDatabaseId": "21"}]},
                            }
                        ]
                    }
                }
            }
        }
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/graphql":
            return httpx2.Response(200, json=thread_states)
        page = request.url.params["page"]
        match request.url.path:
            case "/repos/acme/app/issues/7/comments":
                return httpx2.Response(200, json=full_page if page == "1" else [late_comment])
            case "/repos/acme/app/pulls/7/reviews":
                return httpx2.Response(200, json=reviews)
            case "/repos/acme/app/pulls/7/comments":
                return httpx2.Response(200, json=inline)
            case "/repos/acme/app/pulls/7/commits":
                return httpx2.Response(200, json=commits)
        return httpx2.Response(404)

    github(handler)

    result = await api_get_review_conversation("acme", "app", 7, SESSION)

    items = result.items
    assert len(items) == 104
    commit, first, last = items[0], items[1], items[-1]
    assert isinstance(commit, ConversationCommit)
    assert (commit.sha, commit.message, commit.author and commit.author.bot) == (
        "abc123",
        "Add app",
        True,
    )
    assert isinstance(first, ConversationReview)
    assert (first.id, first.state) == (12, "CHANGES_REQUESTED")
    assert isinstance(last, ConversationComment)
    assert (last.id, last.author, last.body) == (2, None, "")
    approved = items[-2]
    assert isinstance(approved, ConversationReview)
    assert (approved.id, approved.state) == (10, "APPROVED")
    assert 11 not in {getattr(item, "id", None) for item in items}
    assert [item.created_at for item in items] == sorted(item.created_at for item in items)

    resolved, outdated = result.threads
    assert (resolved.id, resolved.node_id, resolved.resolved, resolved.outdated) == (
        21,
        "PRRT_21",
        True,
        False,
    )
    assert [c.id for c in resolved.comments] == [21, 22]
    assert resolved.comments[0].body == "note 21"
    assert (outdated.id, outdated.node_id, outdated.resolved, outdated.outdated) == (
        23,
        None,
        False,
        True,
    )


async def test_post_comment_sends_viewer_token_and_body(
    github: Callable[[Handler], list[httpx2.Request]],
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            201,
            json={
                "id": 99,
                "user": _user("octocat"),
                "created_at": "2026-01-05T00:00:00Z",
                "body": json.loads(request.content)["body"],
                "html_url": "https://github.com/acme/app/pull/7#issuecomment-99",
            },
        )

    seen = github(handler)

    created = await api_post_review_conversation_comment(
        "acme", "app", 7, ConversationCommentCreate(body="  Ship it  "), SESSION
    )

    [request] = seen
    assert request.method == "POST"
    assert request.url.path == "/repos/acme/app/issues/7/comments"
    assert request.headers["Authorization"] == "Bearer viewer-token"
    assert json.loads(request.content) == {"body": "Ship it"}
    assert created.id == 99
    assert created.body == "Ship it"


async def test_post_comment_rejects_blank_body_without_calling_github(
    github: Callable[[Handler], list[httpx2.Request]],
) -> None:
    seen = github(lambda request: httpx2.Response(500))

    with pytest.raises(HTTPException) as exc:
        await api_post_review_conversation_comment(
            "acme", "app", 7, ConversationCommentCreate(body="   "), SESSION
        )

    assert exc.value.status_code == 422
    assert seen == []


async def test_post_comment_surfaces_github_client_errors(
    github: Callable[[Handler], list[httpx2.Request]],
) -> None:
    github(
        lambda request: httpx2.Response(
            403, json={"message": "Resource not accessible by integration"}
        )
    )

    with pytest.raises(GitHubError) as exc:
        await api_post_review_conversation_comment(
            "acme", "app", 7, ConversationCommentCreate(body="hi"), SESSION
        )

    assert exc.value.response.status_code == 403
    assert exc.value.message == "Resource not accessible by integration"
