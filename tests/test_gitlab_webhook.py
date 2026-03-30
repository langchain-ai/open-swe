from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from agent import webapp

_TEST_WEBHOOK_SECRET = "test-gitlab-secret"


def test_generate_thread_id_from_gitlab_issue_is_deterministic() -> None:
    first = webapp.generate_thread_id_from_gitlab_issue(123, 42)
    second = webapp.generate_thread_id_from_gitlab_issue(123, 42)

    assert first == second
    assert len(first) == 36


def test_gitlab_webhook_accepts_issue_note_events(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def fake_process_gitlab_note(payload: dict[str, object]) -> None:
        called["payload"] = payload

    monkeypatch.setattr(webapp, "process_gitlab_note", fake_process_gitlab_note)
    monkeypatch.setattr(webapp, "GITLAB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    payload = {
        "object_kind": "note",
        "user": {"username": "octocat"},
        "project": {"id": 1, "path_with_namespace": "platform/frontend/test-swe"},
        "issue": {"iid": 42, "title": "Build landing page", "description": "Create UI"},
        "object_attributes": {
            "note": "@openswe please handle this",
            "noteable_type": "Issue",
            "url": "http://gitlab.local/platform/frontend/test-swe/-/issues/42#note_1",
        },
    }
    response = client.post(
        "/webhooks/gitlab",
        content=json.dumps(payload).encode(),
        headers={"X-Gitlab-Token": _TEST_WEBHOOK_SECRET, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["payload"] == payload


def test_gitlab_webhook_accepts_commit_note_events(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def fake_process_gitlab_note(payload: dict[str, object]) -> None:
        called["payload"] = payload

    monkeypatch.setattr(webapp, "process_gitlab_note", fake_process_gitlab_note)
    monkeypatch.setattr(webapp, "GITLAB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    payload = {
        "object_kind": "note",
        "user": {"username": "octocat"},
        "project": {"id": 1, "path_with_namespace": "platform/frontend/test-swe"},
        "commit": {
            "id": "abc123",
            "title": "Add endpoint",
            "message": "Add endpoint\n",
        },
        "object_attributes": {
            "note": "@openswe please handle this commit note",
            "noteable_type": "Commit",
            "commit_id": "abc123",
            "url": "http://gitlab.local/platform/frontend/test-swe/-/commit/abc123#note_1",
        },
    }
    response = client.post(
        "/webhooks/gitlab",
        content=json.dumps(payload).encode(),
        headers={"X-Gitlab-Token": _TEST_WEBHOOK_SECRET, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["payload"] == payload


def test_extract_gitlab_issue_iid_falls_back_to_noteable_iid() -> None:
    payload = {
        "issue": {},
        "object_attributes": {
            "noteable_type": "Issue",
            "noteable_iid": "42",
        },
    }

    assert webapp._extract_gitlab_issue_iid(payload) == 42


def test_extract_gitlab_merge_request_iid_falls_back_to_noteable_iid() -> None:
    payload = {
        "merge_request": {},
        "object_attributes": {
            "noteable_type": "MergeRequest",
            "noteable_iid": 7,
        },
    }

    assert webapp._extract_gitlab_merge_request_iid(payload) == 7


def test_process_gitlab_note_commit_creates_run(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_is_thread_active(thread_id: str) -> bool:
        captured["thread_id"] = thread_id
        return False

    class _FakeRunsClient:
        async def create(self, *args, **kwargs) -> None:
            captured["run_args"] = args
            captured["run_kwargs"] = kwargs

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()

    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClient())

    payload = {
        "object_kind": "note",
        "user": {"username": "octocat"},
        "project": {"id": 1898, "path_with_namespace": "swe/java-demo"},
        "commit": {
            "id": "4d7f9355fac35a20fd26d98dae3d356c959f2300",
            "title": "Default Changist",
            "message": "Default Changist\n",
            "url": "http://gitlab.local/swe/java-demo/-/commit/4d7f9355fac35a20fd26d98dae3d356c959f2300",
        },
        "object_attributes": {
            "note": "@openswe add /health-swe",
            "noteable_type": "Commit",
            "commit_id": "4d7f9355fac35a20fd26d98dae3d356c959f2300",
            "url": "http://gitlab.local/swe/java-demo/-/commit/4d7f9355fac35a20fd26d98dae3d356c959f2300#note_1",
        },
    }

    asyncio.run(webapp.process_gitlab_note(payload))

    assert captured["thread_id"] == webapp.generate_thread_id_from_gitlab_commit(
        1898, "4d7f9355fac35a20fd26d98dae3d356c959f2300"
    )
    run_args = captured["run_args"]
    assert run_args[0] == captured["thread_id"]
    assert run_args[1] == "agent"
    run_kwargs = captured["run_kwargs"]
    assert run_kwargs["config"]["configurable"]["gitlab_commit"]["sha"] == (
        "4d7f9355fac35a20fd26d98dae3d356c959f2300"
    )