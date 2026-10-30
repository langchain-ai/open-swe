"""The ID a dashboard error toast shows finds the server side of the failure."""

import logging
from collections import deque

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from openswe.api import request_ids
from openswe.dashboard.client_errors import router as client_errors_router
from openswe.dashboard.oauth import require_session
from openswe.database import transaction
from openswe.webhooks.error_log import ERROR_LOG

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


def test_tracing_failures_do_not_break_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_tags: dict[str, str]) -> None:
        raise RuntimeError("no tracer here")

    monkeypatch.setattr(request_ids, "_tag_root_span", explode)

    assert TestClient(_app()).put("/dashboard/api/settings").status_code == 409


async def test_server_and_frontend_errors_are_persisted_without_changing_responses(
    registry_db: None, span_tags: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ERROR_LOG, "_recorded", deque())
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/dashboard/api/boom", headers={"X-Request-ID": REQUEST_ID})
        assert response.status_code == 500
        response = await client.post(
            "/dashboard/api/client-errors",
            json={"error_id": REQUEST_ID, "title": "Failed", "error_message": "browser failure"},
        )
        assert response.status_code == 204
    async with transaction() as conn:
        rows = (await conn.execute(text("SELECT source, payload FROM event_log"))).all()
    payloads = {row.source: row.payload for row in rows}
    assert set(payloads) == {"internal", "frontend"}
    assert "RuntimeError: boom" in payloads["internal"]["traceback"]
    assert payloads["frontend"]["error_message"] == "browser failure"
    assert payloads["frontend"]["login"] == "alice"


def test_client_error_report_rejects_arbitrary_ids(span_tags: list[dict[str, str]]) -> None:
    response = TestClient(_app()).post(
        "/dashboard/api/client-errors",
        json={"error_id": "anything", "title": "x", "error_message": "y"},
    )

    assert response.status_code == 422
