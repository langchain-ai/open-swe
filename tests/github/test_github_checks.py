from typing import Any

import httpx2
import pytest

from openswe.review import publish as reviewer_publish


class _FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        error: bool = False,
        status_code: int = 200,
    ) -> None:
        self._payload = payload or {}
        self._error = error
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self._error:
            raise httpx2.HTTPStatusError("forbidden", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    last_post: dict[str, Any] | None = None
    last_patch: dict[str, Any] | None = None
    post_response: _FakeResponse = _FakeResponse({"id": 42})
    patch_response: _FakeResponse = _FakeResponse({})

    def __init__(self, **kwargs: Any) -> None:
        pass

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
