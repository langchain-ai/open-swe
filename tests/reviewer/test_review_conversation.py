import json
from collections.abc import Callable

import httpx2
import pytest
from fastapi import HTTPException

from agent.github.checks import github_headers
from agent.review import conversation
from agent.review.conversation import (
    ConversationComment,
    ConversationCommentCreate,
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
    monkeypatch.setattr(conversation, "get_valid_access_token", token)

    def install(handler: Handler) -> list[httpx2.Request]:
        seen: list[httpx2.Request] = []

        def record(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return handler(request)

        def client(token: str) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(
                base_url="https://api.github.com",
                headers=github_headers(token),
                transport=httpx2.MockTransport(record),
            )

        monkeypatch.setattr(conversation, "_client", client)
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
    inline = [
        {"pull_request_review_id": 12},
        {"pull_request_review_id": 12},
        {"pull_request_review_id": 10},
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        page = request.url.params["page"]
        match request.url.path:
            case "/repos/acme/app/issues/7/comments":
                return httpx2.Response(200, json=full_page if page == "1" else [late_comment])
            case "/repos/acme/app/pulls/7/reviews":
                return httpx2.Response(200, json=reviews)
            case "/repos/acme/app/pulls/7/comments":
                return httpx2.Response(200, json=inline)
        return httpx2.Response(404)

    github(handler)

    result = await api_get_review_conversation("acme", "app", 7, SESSION)

    items = result.items
    assert len(items) == 103
    first, last = items[0], items[-1]
    assert isinstance(first, ConversationReview)
    assert (first.id, first.state, first.inline_comment_count) == (12, "CHANGES_REQUESTED", 2)
    assert isinstance(last, ConversationComment)
    assert (last.id, last.author, last.body) == (2, None, "")
    approved = items[-2]
    assert isinstance(approved, ConversationReview)
    assert (approved.id, approved.state, approved.inline_comment_count) == (10, "APPROVED", 1)
    assert 11 not in {item.id for item in items}
    assert [item.created_at for item in items] == sorted(item.created_at for item in items)


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

    with pytest.raises(HTTPException) as exc:
        await api_post_review_conversation_comment(
            "acme", "app", 7, ConversationCommentCreate(body="hi"), SESSION
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Resource not accessible by integration"
