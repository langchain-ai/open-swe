"""APM resource naming for web requests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openswe.api import tracing


@pytest.fixture
def named_resources(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []
    monkeypatch.setattr(tracing, "_rename_root_span", recorded.append)
    return recorded


def _client(app: FastAPI) -> TestClient:
    tracing.add_trace_resource_names(app)
    return TestClient(app)


def test_failing_endpoints_are_named_too(named_resources: list[str]) -> None:
    """The 500 is produced above this middleware, so naming only on the response
    would leave every failing request in the generic resource."""
    app = FastAPI()

    @app.get("/api/threads/page")
    async def explode() -> dict[str, str]:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _client(app).get("/api/threads/page")

    assert named_resources == ["GET /api/threads/page"]


def test_naming_failures_do_not_break_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_resource: str) -> None:
        raise RuntimeError("no tracer here")

    monkeypatch.setattr(tracing, "_rename_root_span", explode)
    app = FastAPI()

    @app.get("/api/options")
    async def options() -> dict[str, bool]:
        return {"ok": True}

    response = _client(app).get("/api/options")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
