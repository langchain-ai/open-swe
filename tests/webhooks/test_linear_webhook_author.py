"""Tests for Linear webhook PR author linking (reuse of the Slack user mapping)."""

import asyncio
import hashlib
from typing import Any
from unittest.mock import AsyncMock, patch

from agent.linear import webhook as linear_webhook
from agent.media import MediaRef, media_refs_from_content


def _media_ref(url: str) -> MediaRef:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return MediaRef(
        path=f"/workspace/.open-swe-media/{digest}.png",
        mime_type="image/png",
        sha256=digest,
        size=1,
        source_url=url,
    )


def _full_issue(*, user_email: str | None = "zhen@example.com", user_name: str = "Zhen") -> dict:
    return {
        "id": "issue-1",
        "title": "Link Linear PRs to author",
        "description": "Do the thing",
        "identifier": "OS-42",
        "url": "https://linear.app/x/issue/OS-42",
        "creator": {"email": user_email, "name": user_name},
        "comments": {"nodes": []},
    }


def _issue_data(*, user_email: str | None, user_name: str = "Zhen") -> dict:
    # linear_webhook attaches comment_author to the issue dict before dispatch.
    data = _full_issue(user_email=user_email, user_name=user_name)
    data["comment_author"] = {"email": user_email, "name": user_name}
    return data


def _run_process(
    issue_data: dict,
    repo_config: dict[str, str],
    *,
    full_issue: dict[str, Any] | None = None,
) -> tuple[dict, dict, str | None, object]:
    captured: dict[str, Any] = {}

    async def fake_dispatch(
        thread_id, content, configurable, *, source, input=None, metadata=None, client=None
    ):
        captured["content"] = input or content
        captured["configurable"] = configurable
        return {"run_id": "run-1"}

    async def fake_upsert(
        thread_id,
        *,
        source,
        repo_config=None,
        github_login="",
        user_email="",
        title="",
        source_context=None,
    ):
        captured["upsert"] = {"github_login": github_login, "user_email": user_email}
        return None

    async def fake_resolve_login(email):
        captured["resolved_email"] = email
        return "zhen" if email == "zhen@example.com" else None

    with (
        patch.object(linear_webhook.common, "react_to_linear_comment", new_callable=AsyncMock),
        patch.object(linear_webhook, "linear_issue_thread_id", return_value="thread-1"),
        patch.object(
            linear_webhook.common,
            "fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=full_issue
            or _full_issue(user_email=issue_data.get("comment_author", {}).get("email")),
        ),
        patch.object(
            linear_webhook.common, "resolve_login_from_email_async", side_effect=fake_resolve_login
        ),
        patch.object(linear_webhook.common, "dispatch_agent_run", side_effect=fake_dispatch),
        patch.object(
            linear_webhook.common, "upsert_agent_thread_metadata", side_effect=fake_upsert
        ),
        patch.object(linear_webhook.common, "post_linear_trace_comment", new_callable=AsyncMock),
        patch.object(
            linear_webhook.common,
            "vision_model_override",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(
            linear_webhook.common,
            "attach_linked_images",
            new_callable=AsyncMock,
            side_effect=lambda thread_id, urls, **_: {url: _media_ref(url) for url in urls},
        ),
    ):
        asyncio.run(linear_webhook.process_linear_issue(issue_data, repo_config))

    return (
        captured.get("configurable", {}),
        captured.get("upsert", {}),
        captured.get("resolved_email"),
        captured.get("content"),
    )


def test_linear_configurable_carries_github_login() -> None:
    configurable, _upsert, resolved_email, _content = _run_process(
        _issue_data(user_email="zhen@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert resolved_email == "zhen@example.com"
    assert configurable["source"] == "linear"
    assert configurable["github_login"] == "zhen"
    assert configurable["user_email"] == "zhen@example.com"


def test_linear_upsert_tags_thread_with_login() -> None:
    _configurable, upsert, _email, _content = _run_process(
        _issue_data(user_email="zhen@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert upsert["github_login"] == "zhen"
    assert upsert["user_email"] == "zhen@example.com"


def test_linear_omits_login_when_unmapped() -> None:
    configurable, upsert, resolved_email, _content = _run_process(
        _issue_data(user_email="nobody@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert resolved_email == "nobody@example.com"
    assert "github_login" not in configurable
    assert upsert["github_login"] == ""


def test_linear_description_images_stay_with_issue_without_comments() -> None:
    issue = _full_issue()
    issue["description"] = "See ![issue](https://example.com/issue.png)"
    _configurable, _upsert, _email, content = _run_process(
        _issue_data(user_email="zhen@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
        full_issue=issue,
    )

    assert isinstance(content, dict)
    messages = content["messages"]
    assert media_refs_from_content(messages[1]["content"]) == [
        _media_ref("https://example.com/issue.png")
    ]


def test_linear_comment_images_stay_with_their_comments() -> None:
    issue = _full_issue()
    issue["comments"]["nodes"] = [
        {
            "id": "comment-1",
            "body": "First ![one](https://example.com/one.png)",
            "user": {"id": "one", "name": "One"},
        },
        {
            "id": "comment-2",
            "body": "Second ![two](https://example.com/two.png)",
            "user": {"id": "two", "name": "Two"},
        },
    ]
    data = _issue_data(user_email="zhen@example.com")
    data["triggering_comment_id"] = "comment-1"
    _configurable, _upsert, _email, content = _run_process(
        data,
        {"owner": "langchain-ai", "name": "open-swe"},
        full_issue=issue,
    )

    assert isinstance(content, dict)
    comment_media = [
        media_refs_from_content(message["content"])
        for message in content["messages"]
        if 'kind="human"' in message["content"]
    ]
    assert comment_media == [
        [_media_ref("https://example.com/one.png")],
        [_media_ref("https://example.com/two.png")],
    ]
