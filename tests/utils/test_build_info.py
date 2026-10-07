"""Build/deploy identity discovery for the backend and its bundled dashboard."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import openswe.utils.build_info as build_info_module
from openswe.utils.build_info import backend_build_info, build_info


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch):
    # Never read the image stamp location from tests.
    monkeypatch.setenv("OPEN_SWE_BUILD_INFO_DIR", "/nonexistent-build-info-dir")
    monkeypatch.delenv("LANGSMITH_LANGGRAPH_GIT_REF_SHA", raising=False)
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


def test_configured_directory_sidecar_wins_over_in_repo_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_backend_sidecar
) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    (tmp_path / "open-swe-build-info.json").write_text(json.dumps({"commit": "img999"}))
    monkeypatch.setenv("OPEN_SWE_BUILD_INFO_DIR", str(tmp_path))
    write_backend_sidecar({"commit": "local111"})
    assert backend_build_info()["commit"] == "img999"


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


@pytest.mark.parametrize("source_commit", ["explicit-image-sha"])
@pytest.mark.parametrize("dashboard_kind", ["bundled", "custom", "replaced", "missing"])
def test_image_stamp_and_dashboard_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_commit: str,
    dashboard_kind: str,
) -> None:
    backend = tmp_path / "backend"
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "_shell.html").write_text("shell")
    stamp = dashboard / "open-swe-build-info.json"
    if dashboard_kind != "missing":
        stamp.write_text(json.dumps({"built_at": "2026-09-22T00:00:00Z"}))
    monkeypatch.setenv("SOURCE_COMMIT", source_commit)
    monkeypatch.setenv("LANGSMITH_LANGGRAPH_GIT_REF_SHA", "platform-sha")
    monkeypatch.setenv("OPEN_SWE_BUILD_INFO_DIR", str(backend))
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(dashboard))
    monkeypatch.setattr(
        build_info_module,
        "_IMAGE_DASHBOARD_DIR",
        tmp_path / "other" if dashboard_kind == "custom" else dashboard,
    )
    before = datetime.now(UTC)
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts" / "stamp_build_info.py"),
            str(backend),
            str(dashboard),
        ],
        check=True,
    )
    if dashboard_kind == "replaced":
        stamp.write_text(json.dumps({"built_at": "2026-09-23T00:00:00Z"}))
    info = build_info()
    assert before <= datetime.fromisoformat(info["backend"]["built_at"]) <= datetime.now(UTC)
    assert info["backend"]["commit"] == (source_commit or "platform-sha")
    assert info["dashboard"]["commit"] == (
        (source_commit or "platform-sha") if dashboard_kind == "bundled" else None
    )
