from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from blockbuster import BlockBuster

from agent.desktop import (
    create_local_backend,
    is_local_run,
    local_artifact_routes,
    resolve_local_run_target,
    resolve_run_metadata,
)

LOCAL_METADATA = {
    "run_location": "local",
    "run_location_login": "octocat",
    "device_id": "abc123",
    "device_name": "Work laptop",
    "local_project_path": "/Users/octocat/dev/app",
}


@contextmanager
def detect_blocking_calls() -> Iterator[None]:
    """Leak-proof ``blockbuster_ctx``: deactivates even when the body raises."""
    blockbuster = BlockBuster()
    blockbuster.activate()
    try:
        yield
    finally:
        blockbuster.deactivate()


def test_a_thread_without_a_run_location_is_a_cloud_thread() -> None:
    assert is_local_run({}) is False
    assert is_local_run({"run_location": "cloud"}) is False
    assert is_local_run(LOCAL_METADATA) is True


def test_local_backend_is_bound_to_the_recorded_device_and_project() -> None:
    backend = create_local_backend(LOCAL_METADATA, "thread-1")
    assert backend.id == "abc123"
    assert backend._login == "octocat"
    assert backend._project_path == "/Users/octocat/dev/app"


def test_a_local_thread_missing_its_binding_is_rejected() -> None:
    incomplete = {**LOCAL_METADATA}
    del incomplete["device_id"]
    with pytest.raises(ValueError, match="device_id"):
        resolve_local_run_target(incomplete)


def test_device_name_falls_back_to_the_device_id() -> None:
    unnamed = {**LOCAL_METADATA, "device_name": ""}
    assert resolve_local_run_target(unnamed).device_name == "abc123"


async def test_cloud_runs_resolve_metadata_without_a_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_thread_id: str) -> dict[str, str]:
        raise AssertionError("a cloud run must not refetch thread metadata")

    monkeypatch.setattr("agent.desktop.load_thread_metadata", fail)
    metadata = await resolve_run_metadata({"metadata": {"title": "x"}}, "thread-1")
    assert metadata == {"title": "x"}


async def test_a_run_claiming_to_be_local_is_checked_against_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stored(thread_id: str) -> dict[str, str]:
        assert thread_id == "thread-1"
        return LOCAL_METADATA

    monkeypatch.setattr("agent.desktop.load_thread_metadata", stored)
    # The device fields must come from what the dashboard stamped, not from the
    # run request, which a client controls.
    metadata = await resolve_run_metadata(
        {
            "metadata": {},
            "configurable": {"run_location": "local", "device_id": "someone-elses"},
        },
        "thread-1",
    )
    assert metadata["device_id"] == "abc123"


async def test_artifact_routes_stay_out_of_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", str(artifacts))

    with detect_blocking_calls():
        routes = await local_artifact_routes("thread-1")
    assert set(routes) == {"/large_tool_results/", "/conversation_history/"}
    for prefix, backend in routes.items():
        root = Path(str(backend.cwd)).resolve()
        assert root.is_dir()
        assert root == (artifacts / "thread-1" / prefix.strip("/")).resolve()


async def test_artifact_routes_reject_a_traversing_thread_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("OPEN_SWE_LOCAL_ARTIFACTS_DIR", str(artifacts))

    routes = await local_artifact_routes("../../etc")
    for backend in routes.values():
        root = Path(str(backend.cwd)).resolve()
        assert artifacts.resolve() in root.parents
