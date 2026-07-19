from __future__ import annotations

import json
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks, Request

from agent.utils import github_feedback
from agent.utils.github_feedback import (
    process_github_reaction_added,
    process_github_reaction_removed,
)
from agent.webhooks import common as webhook_common
from agent.webhooks import github_routes


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}

    async def delete_item(self, namespace: tuple[str, ...], key: str) -> None:
        self.items.pop((namespace, key), None)


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers = {
            "X-GitHub-Event": "reaction",
            "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": "sig",
        }
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


def _reaction_payload(content: str = "+1", action: str = "created") -> dict[str, Any]:
    return {
        "action": action,
        "reaction": {"content": content},
        "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
        "pull_request": {"number": 7},
        "comment": {"id": 123, "pull_request_url": "https://api.github.com/repos/o/r/pulls/7"},
        "sender": {"login": "reviewer"},
    }


@pytest.mark.asyncio
async def test_github_reaction_added_creates_langsmith_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    created: dict[str, Any] = {}

    def fake_create_feedback(
        run_id: str,
        key: str,
        *,
        score: float,
        comment: str | None = None,
        source_info: dict[str, Any] | None = None,
    ) -> bool:
        created.update(
            {
                "run_id": run_id,
                "key": key,
                "score": score,
                "comment": comment,
                "source_info": source_info,
            }
        )
        return True

    monkeypatch.setattr(github_feedback, "get_client", lambda url: client)

    async def fake_list_findings(thread_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "f1",
                "github_review_comment_id": 123,
                "github_review_run_id": "run-1",
            }
        ]

    monkeypatch.setattr(
        github_feedback,
        "list_findings",
        fake_list_findings,
    )
    monkeypatch.setattr(github_feedback, "create_langsmith_feedback", fake_create_feedback)

    await process_github_reaction_added(_reaction_payload(), delivery_id="delivery-1")

    assert created["run_id"] == "run-1"
    assert created["key"] == "github_reaction:langchain-ai/open-swe:reviewer:123"
    assert created["score"] == 1.0
    assert created["source_info"]["finding_id"] == "f1"
    assert (("github_reaction_events", "langchain-ai/open-swe"), "delivery-1") in client.store.items


@pytest.mark.asyncio
async def test_github_reaction_removed_deletes_langsmith_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.store.items[
        (("github_reaction_state", "langchain-ai/open-swe"), "run-1:reviewer:123")
    ] = {
        "value": {
            "run_id": "run-1",
            "user_login": "reviewer",
            "comment_id": 123,
            "reactions": ["+1"],
        }
    }
    deleted: dict[str, str] = {}

    def fake_delete_feedback(run_id: str, key: str) -> bool:
        deleted["run_id"] = run_id
        deleted["key"] = key
        return True

    monkeypatch.setattr(github_feedback, "get_client", lambda url: client)

    async def fake_list_findings(thread_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "f1",
                "github_review_comment_id": 123,
                "github_review_run_id": "run-1",
            }
        ]

    monkeypatch.setattr(
        github_feedback,
        "list_findings",
        fake_list_findings,
    )
    monkeypatch.setattr(github_feedback, "delete_langsmith_feedback", fake_delete_feedback)

    await process_github_reaction_removed(
        _reaction_payload(action="deleted"), delivery_id="delivery-2"
    )

    assert deleted == {
        "run_id": "run-1",
        "key": "github_reaction:langchain-ai/open-swe:reviewer:123",
    }
    assert (
        ("github_reaction_state", "langchain-ai/open-swe"),
        "run-1:reviewer:123",
    ) not in client.store.items


@pytest.mark.asyncio
async def test_github_webhook_ignores_reaction_event(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _reaction_payload()
    background_tasks = _FakeBackgroundTasks()

    monkeypatch.setattr(webhook_common, "verify_github_signature", lambda *args, **kwargs: True)

    response = await github_routes.github_webhook(
        cast(Request, _FakeRequest(payload)), cast(BackgroundTasks, background_tasks)
    )

    assert response == {"status": "ignored", "reason": "Unsupported event type: reaction"}
    assert background_tasks.tasks == []
