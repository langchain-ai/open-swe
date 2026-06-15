from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent import reviewer_publish
from agent.utils import github_checks


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, error: bool = False) -> None:
        self._payload = payload or {}
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise httpx.HTTPStatusError("forbidden", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    last_post: dict[str, Any] | None = None
    last_patch: dict[str, Any] | None = None
    post_response: _FakeResponse = _FakeResponse({"id": 42})
    patch_response: _FakeResponse = _FakeResponse({})

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).last_post = {"url": url, **kwargs}
        return type(self).post_response

    async def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).last_patch = {"url": url, **kwargs}
        return type(self).patch_response


@pytest.fixture(autouse=True)
def _reset_fake_client() -> None:
    _FakeAsyncClient.last_post = None
    _FakeAsyncClient.last_patch = None
    _FakeAsyncClient.post_response = _FakeResponse({"id": 42})
    _FakeAsyncClient.patch_response = _FakeResponse({})


async def test_create_review_check_run_posts_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_checks.httpx, "AsyncClient", _FakeAsyncClient)

    check_run_id = await github_checks.create_review_check_run(
        owner="acme",
        repo="widgets",
        head_sha="abc123",
        token="tok",
        details_url="https://example.com/thread",
    )

    assert check_run_id == 42
    assert _FakeAsyncClient.last_post is not None
    assert _FakeAsyncClient.last_post["url"].endswith("/repos/acme/widgets/check-runs")
    body = _FakeAsyncClient.last_post["json"]
    assert body["name"] == github_checks.REVIEW_CHECK_RUN_NAME
    assert body["head_sha"] == "abc123"
    assert body["status"] == "in_progress"
    assert body["details_url"] == "https://example.com/thread"


async def test_create_review_check_run_returns_none_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_checks.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.post_response = _FakeResponse(error=True)

    check_run_id = await github_checks.create_review_check_run(
        owner="acme", repo="widgets", head_sha="abc123", token="tok"
    )

    assert check_run_id is None


async def test_complete_review_check_run_patches_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_checks.httpx, "AsyncClient", _FakeAsyncClient)

    ok = await github_checks.complete_review_check_run(
        owner="acme",
        repo="widgets",
        check_run_id=42,
        token="tok",
        conclusion="neutral",
        title="Found 2 potential issues",
        summary="…",
    )

    assert ok is True
    assert _FakeAsyncClient.last_patch is not None
    assert _FakeAsyncClient.last_patch["url"].endswith("/repos/acme/widgets/check-runs/42")
    body = _FakeAsyncClient.last_patch["json"]
    assert body["status"] == "completed"
    assert body["conclusion"] == "neutral"
    assert body["output"]["title"] == "Found 2 potential issues"


async def test_post_autofix_status_check_completes_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(github_checks.httpx, "AsyncClient", _FakeAsyncClient)

    ok = await github_checks.post_autofix_status_check(
        owner="acme",
        repo="widgets",
        head_sha="abc123",
        token="tok",
        title="Auto-fixing 1 failing check(s)",
        summary="working on it",
        details_url="https://example.com/thread",
    )

    assert ok is True
    assert _FakeAsyncClient.last_post is not None
    assert _FakeAsyncClient.last_post["url"].endswith("/repos/acme/widgets/check-runs")
    body = _FakeAsyncClient.last_post["json"]
    assert body["name"] == github_checks.AUTOFIX_CHECK_RUN_NAME
    assert body["status"] == "completed"
    assert body["conclusion"] == "neutral"
    assert body["details_url"] == "https://example.com/thread"


def test_review_check_conclusion_mapping() -> None:
    conclusion, title, _ = github_checks.review_check_conclusion(0)
    assert conclusion == "success"
    assert title == "No issues found"

    conclusion, title, _ = github_checks.review_check_conclusion(1)
    assert conclusion == "success"
    assert "1 potential issue" in title

    conclusion, title, _ = github_checks.review_check_conclusion(3)
    assert conclusion == "success"
    assert "3 potential issues" in title


async def test_settle_review_check_run_noop_without_tracked_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_thread_metadata(thread_id: str) -> dict[str, Any]:
        return {}

    completed: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> bool:
        completed.append(kwargs)
        return True

    monkeypatch.setattr(reviewer_publish, "get_thread_metadata", fake_get_thread_metadata)
    monkeypatch.setattr(reviewer_publish, "complete_review_check_run", fake_complete)

    await reviewer_publish.settle_review_check_run(
        thread_id="t1",
        owner="acme",
        repo="widgets",
        token="tok",
        conclusion="success",
        title="t",
        summary="s",
    )

    assert completed == []


async def test_settle_review_check_run_completes_and_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_thread_metadata(thread_id: str) -> dict[str, Any]:
        return {"review_check_run_id": 42}

    completed: list[dict[str, Any]] = []
    metadata_writes: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> bool:
        completed.append(kwargs)
        return True

    async def fake_set_metadata(thread_id: str, **kwargs: Any) -> None:
        metadata_writes.append({"thread_id": thread_id, **kwargs})

    monkeypatch.setattr(reviewer_publish, "get_thread_metadata", fake_get_thread_metadata)
    monkeypatch.setattr(reviewer_publish, "complete_review_check_run", fake_complete)
    monkeypatch.setattr(reviewer_publish, "set_reviewer_thread_metadata", fake_set_metadata)

    await reviewer_publish.settle_review_check_run(
        thread_id="t1",
        owner="acme",
        repo="widgets",
        token="tok",
        conclusion="neutral",
        title="t",
        summary="s",
    )

    assert len(completed) == 1
    assert completed[0]["check_run_id"] == 42
    assert completed[0]["conclusion"] == "neutral"
    assert metadata_writes == [
        {
            "thread_id": "t1",
            "extra": {"review_check_run_id": None, "review_check_pending_result": None},
        }
    ]


async def test_settle_review_check_run_keeps_id_on_patch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_thread_metadata(thread_id: str) -> dict[str, Any]:
        return {"review_check_run_id": 42}

    metadata_writes: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> bool:
        return False

    async def fake_set_metadata(thread_id: str, **kwargs: Any) -> None:
        metadata_writes.append({"thread_id": thread_id, **kwargs})

    monkeypatch.setattr(reviewer_publish, "get_thread_metadata", fake_get_thread_metadata)
    monkeypatch.setattr(reviewer_publish, "complete_review_check_run", fake_complete)
    monkeypatch.setattr(reviewer_publish, "set_reviewer_thread_metadata", fake_set_metadata)

    await reviewer_publish.settle_review_check_run(
        thread_id="t1",
        owner="acme",
        repo="widgets",
        token="tok",
        conclusion="success",
        title="t",
        summary="s",
    )

    assert metadata_writes == [
        {
            "thread_id": "t1",
            "extra": {
                "review_check_pending_result": {
                    "conclusion": "success",
                    "title": "t",
                    "summary": "s",
                }
            },
        }
    ]
