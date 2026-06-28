from __future__ import annotations

import importlib
from typing import Any

import pytest

from agent import delivery_queue as queue

tool = importlib.import_module("agent.tools.submit_delivery_review_result")


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    return client


def _ready_preflight() -> queue.PreflightInput:
    return {
        "active_project": True,
        "readiness": True,
        "issue_context": True,
        "credentials": True,
        "ai_hub_ready": True,
        "sandbox_profile": True,
        "budget": True,
        "duplicate_active_run": False,
        "kill_switch": False,
    }


async def _review_item(**overrides: Any) -> dict[str, Any]:
    payload = {
        "project_id": "sports-cms",
        "provider": "linear",
        "external_work_item_id": "SPORT-1",
        "title": "Fix teaser",
        "description": "Fix Sports CMS teaser.",
        "worker_thread_id": "worker-thread",
        "reviewer_thread_id": "reviewer-thread",
        "qa_thread_id": "qa-thread",
        "credential_identity": "github:user:octocat",
        "credential_policy": {"provider": "github", "scope": "user"},
        "merge_policy": {"enabled": True, "strategy": "squash", "required_checks": ["tests"]},
        "qa_evidence": {
            "complete": True,
            "preview_url": "https://preview.example.test",
            "screenshots": ["https://artifacts.example.test/screen.png"],
            "traces": ["https://artifacts.example.test/trace.zip"],
            "gates": [{"name": "tests", "status": "passed", "source": "platform"}],
        },
        "required_checks": [{"name": "tests", "status": "completed", "conclusion": "success"}],
        "pr": {
            "number": 42,
            "state": "open",
            "draft": False,
            "head": {"sha": "head-sha"},
            "base": {"ref": "main"},
        },
        "repo": {"owner": "example", "name": "sports-cms"},
        "review_result": {
            "status": "pending",
            "reviewer_thread_id": "reviewer-thread",
            "qa_thread_id": "qa-thread",
        },
    }
    payload.update(overrides)
    item = await queue.upsert_delivery_queue_item(payload, preflight=_ready_preflight())
    return await queue.transition_delivery_queue_status(item["id"], "review")


def _review_result() -> dict[str, Any]:
    return {"reviewed_sha": "head-sha", "findings": []}


def _qa_result() -> dict[str, Any]:
    return {"passed": True, "artifacts": ["qa-report.json"]}


@pytest.mark.asyncio
async def test_review_submit_waits_for_required_qa(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = await _review_item()
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"delivery_queue_item_id": item["id"]}},
    )

    result = await tool.submit_delivery_review_result(_review_result())

    updated = await queue.read_delivery_queue_item(item["id"])
    assert result["success"] is True
    assert result["waiting_for"] == "qa_result"
    assert updated["status"] == "review"
    assert updated["review_result"]["status"] == "waiting_for_qa"
    assert updated["delivery_review_submission"] == _review_result()


@pytest.mark.asyncio
async def test_qa_submit_combines_pending_review_and_runs_auto_merge(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = await _review_item(delivery_review_submission=_review_result())
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"delivery_queue_item_id": item["id"]}},
    )

    async def fake_merge(item_id: str, *, token: str | None, **_kwargs: Any) -> dict[str, Any]:
        assert item_id == item["id"]
        assert token == "merge-token"
        return await queue.transition_delivery_queue_status(
            item_id,
            "done",
            reason="merge completed",
            extra={"merge_status": "merged", "merge_commit_sha": "merge-sha"},
        )

    async def fake_token(_item: dict[str, Any]) -> str:
        return "merge-token"

    monkeypatch.setattr(tool, "_resolve_merge_token", fake_token)
    monkeypatch.setattr(tool, "execute_delivery_merge", fake_merge)

    result = await tool.submit_delivery_qa_result(_qa_result())

    updated = await queue.read_delivery_queue_item(item["id"])
    assert result["success"] is True
    assert result["queue_status"] == "done"
    assert result["merge_status"] == "merged"
    assert updated["review_result"]["status"] == "passed"
    assert updated["merge_status"] == "merged"


@pytest.mark.asyncio
async def test_qa_submit_waits_for_review_when_review_missing(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = await _review_item()
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"delivery_queue_item_id": item["id"]}},
    )

    result = await tool.submit_delivery_qa_result(_qa_result())

    updated = await queue.read_delivery_queue_item(item["id"])
    assert result["success"] is True
    assert result["waiting_for"] == "review_result"
    assert updated["delivery_qa_result"] == _qa_result()
    assert updated["review_result"]["status"] == "waiting_for_review"
