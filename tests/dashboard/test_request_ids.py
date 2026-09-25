"""The ID a dashboard error toast shows finds the server side of the failure."""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.api import request_ids
from agent.dashboard.client_errors import router as client_errors_router
from agent.dashboard.oauth import require_session

REQUEST_ID = "req_0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.fixture
def span_tags(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    recorded: list[dict[str, str]] = []
    monkeypatch.setattr(request_ids, "_tag_root_span", recorded.append)
    return recorded


def _app() -> FastAPI:
    app = FastAPI()

    @app.put("/dashboard/api/settings")
    async def save() -> dict[str, bool]:
        raise HTTPException(status_code=409, detail="stale")

    @app.get("/dashboard/api/settings")
    async def read() -> dict[str, bool]:
        raise HTTPException(status_code=404)

    @app.get("/dashboard/api/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("boom")

    app.include_router(client_errors_router, prefix="/dashboard/api")
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    request_ids.add_request_ids(app)
    return app


def _failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == request_ids.__name__]


def test_failed_write_is_logged_and_traced_under_the_browser_id(
    span_tags: list[dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = TestClient(_app()).put(
            "/dashboard/api/settings", headers={"X-Request-ID": REQUEST_ID}
        )

    assert response.status_code == 409
    assert response.headers["x-request-id"] == REQUEST_ID
    assert {"request_id": REQUEST_ID} in span_tags
    assert {"error_id": REQUEST_ID} in span_tags
    [record] = _failures(caplog)
    assert record.__dict__["error_id"] == REQUEST_ID
    assert record.__dict__["http_route"] == "/dashboard/api/settings"
    assert record.__dict__["status_code"] == 409


def test_failed_reads_below_500_are_not_logged(
    span_tags: list[dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = TestClient(_app()).get("/dashboard/api/settings")

    assert response.status_code == 404
    assert _failures(caplog) == []


def test_raising_endpoint_is_logged_with_the_id(
    span_tags: list[dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        TestClient(_app()).get("/dashboard/api/boom", headers={"X-Request-ID": REQUEST_ID})

    [record] = _failures(caplog)
    assert record.__dict__["error_id"] == REQUEST_ID
    assert record.exc_info is not None


def test_malformed_ids_are_replaced(span_tags: list[dict[str, str]]) -> None:
    response = TestClient(_app()).put(
        "/dashboard/api/settings", headers={"X-Request-ID": "req_<script>"}
    )

    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-request-id"] != "req_<script>"


def test_tracing_failures_do_not_break_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_tags: dict[str, str]) -> None:
        raise RuntimeError("no tracer here")

    monkeypatch.setattr(request_ids, "_tag_root_span", explode)

    assert TestClient(_app()).put("/dashboard/api/settings").status_code == 409


def test_client_error_report_is_logged_with_its_id_and_user(
    span_tags: list[dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = TestClient(_app()).post(
            "/dashboard/api/client-errors",
            json={
                "error_id": REQUEST_ID,
                "title": "Couldn't pin thread",
                "error_message": "Couldn't reach the server.",
                "status": 0,
                "mutation": None,
                "path": "/agents",
            },
        )

    assert response.status_code == 204
    [record] = [r for r in caplog.records if r.getMessage() == "Dashboard action failed"]
    assert record.__dict__["error_id"] == REQUEST_ID
    assert record.__dict__["error_title"] == "Couldn't pin thread"
    assert record.__dict__["login"] == "alice"


def test_client_error_report_rejects_arbitrary_ids(span_tags: list[dict[str, str]]) -> None:
    response = TestClient(_app()).post(
        "/dashboard/api/client-errors",
        json={"error_id": "anything", "title": "x", "error_message": "y"},
    )

    assert response.status_code == 422
