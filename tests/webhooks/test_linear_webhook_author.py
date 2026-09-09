"""Linear comment trigger: PR authorship, thread tagging, and image placement."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.linear import sessions
from agent.linear import webhook as linear_webhook
from agent.linear.schema import LinearIssue

_REPO = {"owner": "langchain-ai", "name": "open-swe"}


def _issue(**overrides: Any) -> LinearIssue:
    payload: dict[str, Any] = {
        "id": "issue-1",
        "identifier": "OS-42",
        "title": "Link Linear PRs to author",
        "description": "Do the thing",
        "url": "https://linear.app/x/issue/OS-42",
        "creator": {"email": "zhen@example.com", "name": "Zhen"},
        "comments": {"nodes": []},
    }
    payload.update(overrides)
    return LinearIssue.model_validate(payload)


def _comment(comment_id: str, body: str, **user: Any) -> dict[str, Any]:
    return {"id": comment_id, "body": body, "user": user or {"name": "Zhen"}}


class _Capture:
    def __init__(self) -> None:
        self.configurable: dict[str, Any] = {}
        self.upsert: dict[str, Any] = {}
        self.run_input: dict[str, Any] = {}


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    capture = _Capture()

    async def dispatch(thread_id, content, configurable, **kwargs):
        capture.configurable = configurable
        capture.run_input = kwargs["input"]
        return {"run_id": "run-1"}

    async def upsert(thread_id, **kwargs):
        capture.upsert = kwargs

    async def resolve_login(email):
        return "zhen" if email == "zhen@example.com" else None

    async def image_block(url, _client):
        return {"type": "image_url", "image_url": {"url": url}}

    monkeypatch.setattr(sessions, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(sessions, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(sessions, "resolve_agent_model_id", AsyncMock(return_value="model-1"))
    monkeypatch.setattr(sessions, "model_supports_images", lambda model_id: True)
    monkeypatch.setattr(sessions, "fetch_image_block", image_block)
    monkeypatch.setattr(linear_webhook, "resolve_login_from_email_async", resolve_login)
    monkeypatch.setattr(linear_webhook, "react_to_linear_comment", AsyncMock())
    monkeypatch.setattr(linear_webhook, "post_linear_trace_comment", AsyncMock())
    return capture


async def test_configurable_carries_the_github_login(captured: _Capture) -> None:
    await linear_webhook.process_linear_issue(_issue(), _REPO)

    assert captured.configurable["source"] == "linear"
    assert captured.configurable["github_login"] == "zhen"
    assert captured.configurable["user_email"] == "zhen@example.com"


async def test_thread_metadata_is_tagged_with_the_login(captured: _Capture) -> None:
    await linear_webhook.process_linear_issue(_issue(), _REPO)

    assert captured.upsert["github_login"] == "zhen"
    assert captured.upsert["user_email"] == "zhen@example.com"


async def test_an_unmapped_author_leaves_the_login_out(captured: _Capture) -> None:
    issue = _issue(creator={"email": "nobody@example.com", "name": "Nobody"})

    await linear_webhook.process_linear_issue(issue, _REPO)

    assert "github_login" not in captured.configurable
    assert captured.upsert["github_login"] == ""


async def test_description_images_stay_with_the_issue(captured: _Capture) -> None:
    issue = _issue(description="See ![issue](https://example.com/issue.png)")

    await linear_webhook.process_linear_issue(issue, _REPO)

    system_message = captured.run_input["messages"][1]
    assert system_message["content"][1]["image_url"]["url"] == "https://example.com/issue.png"


async def test_comment_images_stay_with_their_own_comment(captured: _Capture) -> None:
    issue = _issue(
        comments={
            "nodes": [
                _comment("comment-1", "First ![one](https://example.com/one.png)", id="one"),
                _comment("comment-2", "Second ![two](https://example.com/two.png)", id="two"),
            ]
        }
    )
    trigger = issue.comments[0]

    await linear_webhook.process_linear_issue(issue, _REPO, trigger=trigger)

    human_messages = [
        message
        for message in captured.run_input["messages"]
        if isinstance(message["content"], list)
    ]
    assert human_messages[0]["content"][1]["image_url"]["url"].endswith("one.png")
    assert human_messages[1]["content"][1]["image_url"]["url"].endswith("two.png")
