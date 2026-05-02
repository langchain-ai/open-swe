from __future__ import annotations

import asyncio
import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.tools import edit_pull_request as edit_pull_request_tool
from agent.utils import github

edit_pull_request_module = importlib.import_module("agent.tools.edit_pull_request")


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(
        self,
        responses: list[_FakeResponse],
        calls: list[tuple[str, str, dict[str, str], dict[str, str] | None]],
    ) -> None:
        self._responses = responses
        self._calls = calls

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def patch(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self._calls.append(("PATCH", url, headers, json))
        return self._responses.pop(0)


def _config() -> dict[str, Any]:
    return {"configurable": {"repo": {"owner": "owner", "name": "repo"}}, "metadata": {}}


def test_edit_pull_request_requires_repo_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edit_pull_request_module, "get_config", lambda: {"configurable": {}})

    result = edit_pull_request_tool(pr_number=12, title="new title")

    assert result == {
        "success": False,
        "error": "Missing repo owner/name in config",
        "pr_url": None,
    }


def test_edit_pull_request_requires_update_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edit_pull_request_module, "get_config", _config)

    result = edit_pull_request_tool(pr_number=12)

    assert result == {
        "success": False,
        "error": "At least one of title or body must be provided",
        "pr_url": None,
    }


def test_edit_pull_request_prefers_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    edit_mock = AsyncMock(return_value=("https://github.com/owner/repo/pull/12", 12))
    app_token_mock = AsyncMock(return_value="app-token")
    monkeypatch.setattr(edit_pull_request_module, "get_config", _config)
    monkeypatch.setattr(edit_pull_request_module, "get_github_token", lambda config: "user-token")
    monkeypatch.setattr(
        edit_pull_request_module,
        "get_github_app_installation_token",
        app_token_mock,
    )
    monkeypatch.setattr(edit_pull_request_module, "edit_github_pr", edit_mock)

    result = edit_pull_request_tool(pr_number=12, title="new title")

    assert result == {
        "success": True,
        "error": None,
        "pr_url": "https://github.com/owner/repo/pull/12",
    }
    app_token_mock.assert_not_called()
    edit_mock.assert_awaited_once_with(
        repo_owner="owner",
        repo_name="repo",
        github_token="user-token",
        pr_number=12,
        title="new title",
        body=None,
    )


def test_edit_pull_request_falls_back_to_app_token(monkeypatch: pytest.MonkeyPatch) -> None:
    edit_mock = AsyncMock(return_value=("https://github.com/owner/repo/pull/12", 12))
    monkeypatch.setattr(edit_pull_request_module, "get_config", _config)
    monkeypatch.setattr(edit_pull_request_module, "get_github_token", lambda config: None)
    monkeypatch.setattr(
        edit_pull_request_module,
        "get_github_app_installation_token",
        AsyncMock(return_value="app-token"),
    )
    monkeypatch.setattr(edit_pull_request_module, "edit_github_pr", edit_mock)

    result = edit_pull_request_tool(pr_number=12, body="new body")

    assert result["success"] is True
    edit_mock.assert_awaited_once_with(
        repo_owner="owner",
        repo_name="repo",
        github_token="app-token",
        pr_number=12,
        title=None,
        body="new body",
    )


def test_edit_pull_request_fails_without_any_token(monkeypatch: pytest.MonkeyPatch) -> None:
    edit_mock = AsyncMock()
    monkeypatch.setattr(edit_pull_request_module, "get_config", _config)
    monkeypatch.setattr(edit_pull_request_module, "get_github_token", lambda config: None)
    monkeypatch.setattr(
        edit_pull_request_module,
        "get_github_app_installation_token",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(edit_pull_request_module, "edit_github_pr", edit_mock)

    result = edit_pull_request_tool(pr_number=12, title="new title")

    assert result == {"success": False, "error": "Missing GitHub token", "pr_url": None}
    edit_mock.assert_not_called()


def test_edit_github_pr_sends_partial_patch_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, str], dict[str, str] | None]] = []
    responses = [_FakeResponse(200, {"html_url": "https://github.com/o/r/pull/12", "number": 12})]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.edit_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            pr_number=12,
            title="new title",
        )
    )

    assert result == ("https://github.com/o/r/pull/12", 12)
    assert calls == [
        (
            "PATCH",
            "https://api.github.com/repos/o/r/pulls/12",
            {
                "Authorization": "Bearer token",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            {"title": "new title"},
        )
    ]


def test_edit_github_pr_returns_none_on_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, str], dict[str, str] | None]] = []
    responses = [_FakeResponse(404, {"message": "Not Found"})]
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda: _FakeAsyncClient(responses, calls))

    result = asyncio.run(
        github.edit_github_pr(
            repo_owner="o",
            repo_name="r",
            github_token="token",
            pr_number=12,
            body="new body",
        )
    )

    assert result == (None, None)
