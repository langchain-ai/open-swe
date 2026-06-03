from __future__ import annotations

from typing import Any

import pytest

from agent.dashboard import thread_api


@pytest.mark.asyncio
async def test_resolve_repo_config_parses_request_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("profile lookup should be skipped when request carries a repo")

    monkeypatch.setattr(thread_api, "get_profile_default_repo", _fail)
    monkeypatch.setattr(thread_api, "get_profile", _fail)

    assert await thread_api._resolve_repo_config("octocat", "octo/repo") == {
        "owner": "octo",
        "name": "repo",
    }


@pytest.mark.asyncio
async def test_resolve_repo_config_uses_profile_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _default(_login: str | None) -> dict[str, str] | None:
        return {"owner": "octo", "name": "default-repo"}

    monkeypatch.setattr(thread_api, "get_profile_default_repo", _default)

    assert await thread_api._resolve_repo_config("octocat", None) == {
        "owner": "octo",
        "name": "default-repo",
    }


@pytest.mark.asyncio
async def test_resolve_repo_config_returns_empty_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_default(_login: str | None) -> dict[str, str] | None:
        return None

    async def _empty_profile(_login: str) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(thread_api, "get_profile_default_repo", _no_default)
    monkeypatch.setattr(thread_api, "get_profile", _empty_profile)

    # No request repo and no profile default: optional, so no error is raised.
    assert await thread_api._resolve_repo_config("octocat", None) == {}


def test_thread_summary_blanks_repo_when_absent() -> None:
    summary = thread_api._thread_summary(
        {"thread_id": "t1", "metadata": {"source": "dashboard", "title": "no repo run"}}
    )
    assert summary["repo"] == ""
    assert summary["repoFullName"] == ""


def test_thread_summary_keeps_repo_when_present() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "t2",
            "metadata": {
                "source": "dashboard",
                "title": "repo run",
                "repo_owner": "octo",
                "repo_name": "repo",
            },
        }
    )
    assert summary["repo"] == "repo"
    assert summary["repoFullName"] == "octo/repo"
