"""APM resource naming for dashboard requests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.api import tracing


@pytest.fixture
def named_resources(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []
    monkeypatch.setattr(tracing, "_rename_root_span", recorded.append)
    return recorded


def _client(app: FastAPI) -> TestClient:
    tracing.add_trace_resource_names(app)
    return TestClient(app)


def test_resource_is_the_matched_route_not_the_request_path(named_resources: list[str]) -> None:
    app = FastAPI()

    @app.get("/dashboard/api/threads/{thread_id}")
    async def read_thread(thread_id: str) -> dict[str, str]:
        return {"id": thread_id}

    assert _client(app).get("/dashboard/api/threads/t1").status_code == 200
    assert named_resources == ["GET /dashboard/api/threads/{thread_id}"]


def test_unmatched_paths_keep_the_tracer_default(named_resources: list[str]) -> None:
    app = FastAPI()

    assert _client(app).get("/nope").status_code == 404
    assert named_resources == []


def test_failing_endpoints_are_named_too(named_resources: list[str]) -> None:
    """The 500 is produced above this middleware, so naming only on the response
    would leave every failing request in the generic resource."""
    app = FastAPI()

    @app.get("/dashboard/api/threads/page")
    async def explode() -> dict[str, str]:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _client(app).get("/dashboard/api/threads/page")

    assert named_resources == ["GET /dashboard/api/threads/page"]


def test_naming_failures_do_not_break_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_resource: str) -> None:
        raise RuntimeError("no tracer here")

    monkeypatch.setattr(tracing, "_rename_root_span", explode)
    app = FastAPI()

    @app.get("/dashboard/api/options")
    async def options() -> dict[str, bool]:
        return {"ok": True}

    response = _client(app).get("/dashboard/api/options")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
