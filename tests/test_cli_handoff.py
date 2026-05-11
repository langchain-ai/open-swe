"""Tests for /cli/runs/{id}/handoff and /cli/runs/adopt routes."""

from __future__ import annotations

import importlib
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from agent import webapp
from agent.middleware import cli_auth as cli_auth_module
from agent.utils import cli_session as cli_session_module
from agent.utils import handoff as handoff_module

_TEST_SECRET = "test-cli-session-secret-for-pytest-only"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setenv("CLI_SESSION_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ALLOWED_GITHUB_ORG", "langchain-ai")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv1.test")
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    importlib.reload(cli_session_module)
    importlib.reload(cli_auth_module)
    importlib.reload(webapp)
    cli_auth_module._ORG_MEMBERSHIP_CACHE.clear()
    yield
    cli_auth_module._ORG_MEMBERSHIP_CACHE.clear()


def _issue_token(login: str) -> str:
    now = int(time.time())
    payload = {"sub": login, "iat": now, "exp": now + 3600}
    return jwt.encode(payload, _TEST_SECRET, algorithm="HS256")


def _mock_membership(monkeypatch, *, member: bool = True) -> None:
    async def fake_is_member(username: str, org: str) -> bool:  # noqa: ARG001
        return member

    monkeypatch.setattr(cli_auth_module, "is_user_active_org_member", fake_is_member)

    async def fake_identity(login: str):  # noqa: ARG001
        return None

    monkeypatch.setattr(webapp, "get_identities_for_github_login", fake_identity)


def _sample_bundle() -> dict[str, Any]:
    return {
        "thread_id": "src-thread",
        "source": "local",
        "taken_at": "2025-01-01T00:00:00+00:00",
        "conversation": [
            {"type": "human", "content": "do the thing"},
            {"type": "ai", "content": "ok"},
        ],
        "pending_queue": [{"content": "and another"}],
        "git": {
            "remote_url": "https://github.com/octo/repo.git",
            "branch": "feature",
            "head_sha": "deadbeef",
            "uncommitted_diff": "",
            "untracked_files": [],
        },
        "agent": {"model": "anthropic:claude-opus-4-7"},
    }


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_handoff_unauthenticated_returns_401() -> None:
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/abc/handoff")
    assert response.status_code == 401


def test_adopt_unauthenticated_returns_401() -> None:
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/adopt", json={"bundle": _sample_bundle()})
    assert response.status_code == 401


def test_handoff_non_owner_returns_403(monkeypatch) -> None:
    _mock_membership(monkeypatch, member=True)

    async def fake_get_metadata(thread_id: str):  # noqa: ARG001
        return {"cli_owner_login": "someoneelse"}

    monkeypatch.setattr(webapp, "_get_thread_metadata_safe", fake_get_metadata)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/abc/handoff", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Provider gating
# ---------------------------------------------------------------------------


def test_handoff_returns_501_for_non_langsmith(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "modal")
    _mock_membership(monkeypatch, member=True)
    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/abc/handoff", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 501


def test_adopt_returns_501_for_non_langsmith(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "modal")
    _mock_membership(monkeypatch, member=True)
    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post(
        "/cli/runs/adopt",
        json={"bundle": _sample_bundle()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 501


# ---------------------------------------------------------------------------
# Bundle validation
# ---------------------------------------------------------------------------


def test_adopt_rejects_invalid_bundle(monkeypatch) -> None:
    _mock_membership(monkeypatch, member=True)
    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    bad = {"source": "local"}  # missing conversation + git
    response = client.post(
        "/cli/runs/adopt",
        json={"bundle": bad},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_adopt_rejects_oversize_bundle(monkeypatch) -> None:
    _mock_membership(monkeypatch, member=True)
    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    big_bundle = _sample_bundle()
    # Pad uncommitted_diff to exceed the 5MB ceiling.
    big_bundle["git"]["uncommitted_diff"] = "x" * (6 * 1024 * 1024)
    response = client.post(
        "/cli/runs/adopt",
        json={"bundle": big_bundle},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# Handoff: 409 on uninterruptible run
# ---------------------------------------------------------------------------


def test_handoff_returns_409_when_run_does_not_pause(monkeypatch) -> None:
    _mock_membership(monkeypatch, member=True)

    async def fake_get_metadata(thread_id: str):  # noqa: ARG001
        return {"cli_owner_login": "octocat"}

    monkeypatch.setattr(webapp, "_get_thread_metadata_safe", fake_get_metadata)

    async def never_pauses(client, thread_id, timeout_seconds=30.0):  # noqa: ARG001
        return False

    monkeypatch.setattr(handoff_module, "wait_for_run_pause", never_pauses)

    # Ensure the cached webapp also resolves to the patched module.
    monkeypatch.setattr("agent.utils.handoff.wait_for_run_pause", never_pauses, raising=True)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post("/cli/runs/abc/handoff", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Bundle round-trip with mocked sandbox + langgraph SDK
# ---------------------------------------------------------------------------


def _make_fake_client() -> MagicMock:
    fake = MagicMock()
    fake.threads = MagicMock()
    fake.threads.get = AsyncMock(return_value={"status": "idle"})
    fake.threads.update = AsyncMock(return_value=None)
    fake.threads.create = AsyncMock(return_value=None)
    fake.threads.get_state = AsyncMock(
        return_value={"values": {"messages": [{"type": "human", "content": "hi"}]}}
    )
    fake.runs = MagicMock()
    fake.runs.list = AsyncMock(return_value=[{"run_id": "r1"}])
    fake.runs.cancel = AsyncMock(return_value=None)
    fake.runs.create = AsyncMock(return_value=None)
    fake.store = MagicMock()
    fake.store.get_item = AsyncMock(return_value=None)
    fake.store.put_item = AsyncMock(return_value=None)
    return fake


def test_handoff_returns_bundle_with_mocked_sandbox(monkeypatch) -> None:
    _mock_membership(monkeypatch, member=True)

    async def fake_get_metadata(thread_id: str):  # noqa: ARG001
        return {
            "cli_owner_login": "octocat",
            "repo": {"owner": "octo", "name": "repo"},
        }

    monkeypatch.setattr(webapp, "_get_thread_metadata_safe", fake_get_metadata)

    fake = _make_fake_client()
    monkeypatch.setattr(webapp, "get_client", lambda url: fake)

    # Mock the sandbox + work_dir resolution.
    fake_sandbox = object()

    async def fake_ensure(thread_id):  # noqa: ARG001
        return fake_sandbox

    async def fake_workdir(sandbox):  # noqa: ARG001
        return "/workspace"

    import agent.server as server_module

    monkeypatch.setattr(server_module, "ensure_sandbox_for_thread", fake_ensure)
    monkeypatch.setattr("agent.utils.sandbox_paths.aresolve_sandbox_work_dir", fake_workdir)

    async def fake_git_state(sandbox, work_dir):  # noqa: ARG001
        return {
            "repo_dir": "/workspace/repo",
            "remote_url": "https://github.com/octo/repo.git",
            "branch": "main",
            "head_sha": "abc123",
            "uncommitted_diff": "diff --git a/x b/x\n",
            "untracked_files": [],
        }

    monkeypatch.setattr("agent.utils.handoff.build_git_state", fake_git_state)

    async def yes_pauses(client, thread_id, timeout_seconds=30.0):  # noqa: ARG001
        return True

    monkeypatch.setattr("agent.utils.handoff.wait_for_run_pause", yes_pauses)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post(
        "/cli/runs/thread-1/handoff", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["thread_id"] == "thread-1"
    assert body["source"] == "cloud"
    assert body["git"]["remote_url"] == "https://github.com/octo/repo.git"
    assert body["git"]["head_sha"] == "abc123"
    assert body["conversation"]


def test_adopt_creates_new_thread(monkeypatch) -> None:
    _mock_membership(monkeypatch, member=True)

    fake = _make_fake_client()
    monkeypatch.setattr(webapp, "get_client", lambda url: fake)

    fake_sandbox = object()

    async def fake_ensure(thread_id):  # noqa: ARG001
        return fake_sandbox

    async def fake_workdir(sandbox):  # noqa: ARG001
        return "/workspace"

    async def fake_apply(sandbox, work_dir, bundle, token):  # noqa: ARG001
        return None

    async def fake_token():
        return "ghs_dummy"

    import agent.server as server_module

    monkeypatch.setattr(server_module, "ensure_sandbox_for_thread", fake_ensure)
    monkeypatch.setattr("agent.utils.sandbox_paths.aresolve_sandbox_work_dir", fake_workdir)
    monkeypatch.setattr("agent.utils.handoff.apply_bundle_to_sandbox", fake_apply)
    monkeypatch.setattr("agent.utils.github_app.get_github_app_installation_token", fake_token)

    async def fake_queue(thread_id, content):  # noqa: ARG001
        return True

    monkeypatch.setattr(webapp, "queue_message_for_thread", fake_queue)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post(
        "/cli/runs/adopt",
        json={"bundle": _sample_bundle()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "thread_id" in body
    # Should have created the new thread with cli_owner_login metadata.
    create_call = fake.threads.create.await_args
    assert create_call is not None
    metadata = create_call.kwargs.get("metadata", {})
    assert metadata["cli_owner_login"] == "octocat"
    assert metadata["repo"] == {"owner": "octo", "name": "repo"}
    assert metadata["source"] == "cli"
    assert "adopted_from_local_at" in metadata
    # Run was created.
    assert fake.runs.create.await_count == 1


# ---------------------------------------------------------------------------
# Defensive guards
# ---------------------------------------------------------------------------


def test_is_unsafe_path_rejects_escapes() -> None:
    """apply_bundle_to_sandbox must refuse to write outside the repo dir."""
    from agent.utils.handoff import _is_unsafe_path

    assert _is_unsafe_path("../../etc/passwd") is True
    assert _is_unsafe_path("/etc/passwd") is True
    assert _is_unsafe_path("a/../../b") is True
    assert _is_unsafe_path("") is True
    assert _is_unsafe_path("src/foo.py") is False
    assert _is_unsafe_path("./src/foo.py") is False
    assert _is_unsafe_path("a/b/../c") is False  # net depth = 2, safe


def test_adopt_rolls_back_thread_on_apply_failure(monkeypatch) -> None:
    """If apply_bundle_to_sandbox fails, the half-created thread is deleted."""
    _mock_membership(monkeypatch, member=True)

    fake = _make_fake_client()
    fake.threads.delete = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "get_client", lambda url: fake)

    fake_sandbox = object()

    async def fake_ensure(thread_id):  # noqa: ARG001
        return fake_sandbox

    async def fake_workdir(sandbox):  # noqa: ARG001
        return "/workspace"

    async def failing_apply(sandbox, work_dir, bundle, token):  # noqa: ARG001
        raise RuntimeError("git apply blew up")

    async def fake_token():
        return "ghs_dummy"

    import agent.server as server_module

    monkeypatch.setattr(server_module, "ensure_sandbox_for_thread", fake_ensure)
    monkeypatch.setattr("agent.utils.sandbox_paths.aresolve_sandbox_work_dir", fake_workdir)
    monkeypatch.setattr("agent.utils.handoff.apply_bundle_to_sandbox", failing_apply)
    monkeypatch.setattr("agent.utils.github_app.get_github_app_installation_token", fake_token)

    token = _issue_token("octocat")
    client = TestClient(webapp.app)
    response = client.post(
        "/cli/runs/adopt",
        headers={"Authorization": f"Bearer {token}"},
        json={"bundle": _sample_bundle()},
    )
    assert response.status_code == 500
    # The thread should have been created and then rolled back.
    assert fake.threads.create.await_count == 1
    assert fake.threads.delete.await_count == 1
