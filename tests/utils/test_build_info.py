"""Build/deploy identity discovery for the backend and its bundled dashboard."""

import json
from pathlib import Path

import pytest

import agent.utils.build_info as build_info_module
from agent.utils.build_info import backend_build_info, build_info, dashboard_build_info


@pytest.fixture(autouse=True)
def _fresh_caches():
    build_info_module.backend_build_info.cache_clear()
    build_info_module.dashboard_build_info.cache_clear()
    yield
    build_info_module.backend_build_info.cache_clear()
    build_info_module.dashboard_build_info.cache_clear()


@pytest.fixture
def write_backend_sidecar():
    path = Path(build_info_module.__file__).parent / "open-swe-build-info.json"
    assert not path.exists()
    written = False

    def write(data: object) -> None:
        nonlocal written
        path.write_text(json.dumps(data))
        written = True

    yield write
    if written:
        path.unlink()


def test_backend_reports_only_discovered_identifiers(
    monkeypatch: pytest.MonkeyPatch, write_backend_sidecar
) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    write_backend_sidecar({"commit": "abc123", "built_at": "2026-01-01T00:00:00Z"})
    info = backend_build_info()
    assert info["commit"] == "abc123"
    assert info["built_at"] == "2026-01-01T00:00:00Z"
    assert info["revision_id"] is None
    assert info["package_version"] is not None


def test_revision_id_is_reported_as_revision_not_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGCHAIN_REVISION_ID", "rev-987")
    info = backend_build_info()
    assert info["revision_id"] == "rev-987"
    assert info["commit"] is None


def test_unreadable_sidecar_means_unavailable(
    monkeypatch: pytest.MonkeyPatch, write_backend_sidecar
) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    write_backend_sidecar(["not", "an", "object"])
    assert backend_build_info()["commit"] is None


def test_dashboard_sidecar_comes_from_the_served_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_backend_sidecar
) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    (tmp_path / "_shell.html").write_text("<!doctype html>shell")
    (tmp_path / "open-swe-build-info.json").write_text(
        json.dumps({"commit": "def456", "built_at": "2026-02-02T00:00:00Z"})
    )
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    write_backend_sidecar({"commit": "abc123"})
    info = build_info()
    assert info["dashboard"] == {
        "commit": "def456",
        "built_at": "2026-02-02T00:00:00Z",
        "served": True,
    }
    assert info["backend"]["commit"] == "abc123"


def test_dashboard_without_bundle_or_stamp_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DASHBOARD_STATIC_DIR", raising=False)
    (tmp_path / "_shell.html").write_text("<!doctype html>shell")
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    dashboard = dashboard_build_info()
    assert dashboard["served"] is True
    assert dashboard["commit"] is None
    assert dashboard["built_at"] is None


def test_no_bundle_at_all_reports_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A named, existing directory with no build, so the outcome cannot depend
    # on whether an in-repo bundle happens to be present on this machine.
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    assert dashboard_build_info()["served"] is False


@pytest.mark.asyncio
async def test_me_exposes_build_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from fastapi import FastAPI

    from agent.dashboard import oauth, routes

    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    # An empty directory, so the outcome cannot depend on an in-repo bundle.
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: {"sub": "user"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/dashboard/api/me")
    assert response.status_code == 200
    info = response.json()["build_info"]
    assert info["backend"]["revision_id"] is None
    assert info["backend"]["package_version"] is not None
    assert info["dashboard"]["served"] is False
