from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.github import pull_request_status
from openswe.threads import handlers
from tests.conftest import patch_thread_module


def test_pull_request_identity_rejects_untrusted_path_components() -> None:
    assert pull_request_status.pull_request_identity(
        {"repo_full_name": "owner/repo", "number": 7}
    ) == ("owner", "repo", 7)
    assert (
        pull_request_status.pull_request_identity(
            {"repo_full_name": "owner/repo/../../users", "number": 7}
        )
        is None
    )
    assert (
        pull_request_status.pull_request_identity({"repo_full_name": "owner/repo", "number": True})
        is None
    )
    assert (
        pull_request_status.pull_request_identity({"repo_full_name": "owner/..", "number": 7})
        is None
    )


async def test_thread_status_only_reads_pull_requests_the_viewer_can_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    records = [
        {"repo_full_name": "o/one", "number": 1},
        {"repo_full_name": "o/hidden", "number": 2},
    ]

    async def readable(thread_id: str, *, login: str | None = None, email: str | None = None):
        order.append("readable")
        assert (thread_id, login, email) == ("thread-1", "teammate", "teammate@example.com")
        return {"pull_requests": records}

    async def accessible(login: str) -> frozenset[str]:
        order.append("accessible")
        return frozenset({"o/one"})

    mirrored = AsyncMock(return_value=None)
    patch_thread_module(monkeypatch, "_readable_thread_metadata", readable)
    patch_thread_module(monkeypatch, "accessible_repo_full_names", accessible)
    monkeypatch.setattr(handlers.PullRequestSync, "mirrored", mirrored)

    result = await handlers.get_dashboard_thread_pull_request_status(
        "thread-1", "teammate", email="teammate@example.com"
    )

    assert order == ["readable", "accessible"]
    assert mirrored.await_count == 1
    assert [status["repoFullName"] for status in result["pullRequests"]] == ["o/one", "o/hidden"]
    assert not any(status["statusAvailable"] for status in result["pullRequests"])


async def test_thread_status_read_denial_reads_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def denied(*args, **kwargs):
        raise HTTPException(403, "thread is not readable")

    accessible = AsyncMock(return_value=frozenset())
    patch_thread_module(monkeypatch, "_readable_thread_metadata", denied)
    patch_thread_module(monkeypatch, "accessible_repo_full_names", accessible)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.get_dashboard_thread_pull_request_status("thread-1", "intruder")

    assert exc_info.value.status_code == 403
    accessible.assert_not_awaited()
