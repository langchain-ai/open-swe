import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from openswe.dashboard import profiles
from openswe.threads import runs as thread_runs
from openswe.threads.identity import ThreadIdentity
from openswe.users import User
from tests.conftest import patch_thread_module


class _FakeThreadsClient:
    async def create(
        self, *, thread_id: str, metadata: dict[str, Any], if_exists: str
    ) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata, "if_exists": if_exists}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": metadata}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": {}}


class _FakeRunsClient:
    def __init__(self) -> None:
        self.configurable: dict[str, Any] | None = None

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: dict[str, Any],
        config: dict[str, Any],
        if_not_exists: str = "reject",
        stream_mode: list[str] | None = None,
        stream_resumable: bool = False,
    ) -> dict[str, str]:
        self.configurable = config["configurable"]
        return {"run_id": "run-id"}


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.threads = _FakeThreadsClient()
        self.runs = _FakeRunsClient()


@pytest.fixture
def dashboard_run_client(monkeypatch: pytest.MonkeyPatch) -> _FakeLangGraphClient:
    client = _FakeLangGraphClient()
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value="token"))

    async def fake_get_profile(login: str) -> dict[str, Any]:
        return {}

    async def fake_ensure_token(login: str) -> None:
        return None

    async def fake_resolve_email(login: str, profile: dict[str, Any]) -> str:
        return "octo@example.com"

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_profile", fake_get_profile)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", fake_ensure_token)
    patch_thread_module(monkeypatch, "resolve_run_email", fake_resolve_email)
    return client


def test_build_configurable_marks_repo_less_config_when_explicit(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    configurable = asyncio.run(
        thread_runs._build_dashboard_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repo_explicitly_none": True},
        )
    )
    assert configurable["repo_explicitly_none"] is True
    assert "repo" not in configurable


def test_build_configurable_includes_repo_when_configured(
    dashboard_run_client: _FakeLangGraphClient,
) -> None:
    configurable = asyncio.run(
        thread_runs._build_dashboard_configurable(
            "thread-id",
            "octo",
            {"source": "dashboard", "repo_owner": "octo", "repo_name": "repo"},
        )
    )
    assert configurable["repo"] == {"owner": "octo", "name": "repo"}
    assert "repo_explicitly_none" not in configurable


@pytest.mark.asyncio
async def test_revoked_github_starts_chat_without_repository_access(
    monkeypatch, dashboard_run_client
):
    user = User(id=UUID("00000000-0000-0000-0000-000000000001"))
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value=None))
    monkeypatch.setattr(ThreadIdentity, "user", AsyncMock(return_value=user))
    configurable = await thread_runs._build_dashboard_configurable(
        "thread-id", "octo", {"source": "dashboard", "repo_owner": "octo", "repo_name": "repo"}
    )
    assert configurable["github_linked"] is False
    assert configurable["user_id"] == str(user.id)
    assert configurable["repo_explicitly_none"] is True
    assert "repo" not in configurable
    assert "github_login" not in configurable


@pytest.mark.asyncio
async def test_revoked_github_does_not_apply_default_repository(monkeypatch, dashboard_run_client):
    from openswe.webhooks import common

    monkeypatch.setattr(
        thread_runs, "get_profile", AsyncMock(return_value={"default_repo": "octo/repo"})
    )
    monkeypatch.setattr(common, "account_link_reason", AsyncMock(return_value="revoked"))
    monkeypatch.setattr(
        thread_runs, "_resolve_requested_workspace", AsyncMock(return_value="default")
    )
    require_repo = AsyncMock()
    monkeypatch.setattr(thread_runs, "require_repo_access_for_user", require_repo)
    create = AsyncMock(return_value={"thread_id": "thread-id"})
    monkeypatch.setattr(thread_runs, "create_dashboard_thread_record", create)
    await thread_runs.create_dashboard_session(
        thread_runs.SessionCreateBody(prompt="hello"), "octo"
    )
    require_repo.assert_not_awaited()
    assert create.await_args.kwargs["repo_config"] == {}
