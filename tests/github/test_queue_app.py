"""The controller cannot use an agent App or a broadly scoped token."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from openswe.github.queue_app import QueueApp
from tests.support.github_sdk import mock_github_sdk


@pytest.mark.parametrize("fault", [None, "app", "owner", "repository", "permissions", "actor"])
async def test_queue_app_identity_and_scope(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "99")
    permissions = {
        "actions": "write",
        "checks": "read",
        "contents": "write",
        "metadata": "read",
        "pull_requests": "read",
        "statuses": "read",
        "variables": "read",
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app":
            return httpx.Response(
                200,
                json={
                    "id": 999 if fault == "app" else 123,
                    "slug": "queue",
                    "owner": {"login": "acme"},
                },
            )
        if request.url.path == "/app/installations/456":
            return httpx.Response(
                200,
                json={
                    "app_id": 123,
                    "account": {"login": "other" if fault == "owner" else "acme"},
                    "repository_selection": "selected",
                    "suspended_at": None,
                },
            )
        if request.url.path == "/app/installations/456/access_tokens":
            assert json.loads(request.content) == {
                "repository_ids": [42],
                "permissions": permissions,
            }
            return httpx.Response(
                201,
                json={
                    "token": "isolated-token",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "repositories": [
                        {"id": 43 if fault == "repository" else 42, "full_name": "acme/widgets"}
                    ],
                    "permissions": {**permissions, "pull_requests": "write"}
                    if fault == "permissions"
                    else permissions,
                },
            )
        assert request.url.path == "/users/queue[bot]"
        assert request.headers["Authorization"].endswith(" isolated-token")
        return httpx.Response(200, json={"id": 1 if fault == "actor" else 789, "type": "Bot"})

    mock_github_sdk(monkeypatch, respond)
    app = QueueApp(123, 456, 42, "acme/widgets", 789, SecretStr("private-key"))
    if fault:
        with pytest.raises(ValueError):
            await app.token()
    else:
        assert await app.token() == "isolated-token"
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        with pytest.raises(ValueError, match="separate"):
            await app.token()


def test_queue_key_disables_local_agent_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    from openswe.sandboxes.providers.local import create_local_sandbox

    monkeypatch.setenv("MERGE_QUEUE_APP_PRIVATE_KEY", "private-key")
    with pytest.raises(ValueError, match="queue credentials"):
        create_local_sandbox()
