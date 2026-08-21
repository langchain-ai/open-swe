from contextlib import AbstractContextManager
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from daytona import DaytonaNotFoundError

from agent.integrations.daytona import DaytonaBackend, DaytonaProvider, _get_daytona_sandbox_params
from agent.utils.sandbox import SandboxGoneError


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "api-key")


def _daytona_client(**attrs: Any) -> AbstractContextManager[MagicMock]:
    client = MagicMock()
    client.configure_mock(**attrs)
    return cast(
        AbstractContextManager[MagicMock],
        patch("agent.integrations.daytona.Daytona", return_value=client),
    )


def test_daytona_params_default_to_existing_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAYTONA_SANDBOX_SNAPSHOT", raising=False)

    assert _get_daytona_sandbox_params().snapshot == "daytonaio/sandbox:0.6.0"


def test_daytona_params_use_env_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_SANDBOX_SNAPSHOT", "custom/snapshot:1.0")

    assert _get_daytona_sandbox_params().snapshot == "custom/snapshot:1.0"


def test_daytona_params_reject_empty_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_SANDBOX_SNAPSHOT", "  ")

    with pytest.raises(ValueError, match="DAYTONA_SANDBOX_SNAPSHOT must not be empty"):
        _get_daytona_sandbox_params()


async def test_connect_reports_a_deleted_sandbox_as_gone() -> None:
    with _daytona_client(get=MagicMock(side_effect=DaytonaNotFoundError("no such sandbox"))):
        with pytest.raises(SandboxGoneError, match="sandbox-gone"):
            await DaytonaProvider().connect("sandbox-gone")


async def test_connect_keeps_other_failures_untyped() -> None:
    with _daytona_client(get=MagicMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError) as excinfo:
            await DaytonaProvider().connect("sandbox-1")

    assert not isinstance(excinfo.value, SandboxGoneError)


async def test_connect_wraps_the_sdk_sandbox() -> None:
    sdk_sandbox = MagicMock(id="sandbox-1")
    with _daytona_client(get=MagicMock(return_value=sdk_sandbox)) as daytona_class:
        backend = await DaytonaProvider().connect("sandbox-1")

    assert isinstance(backend, DaytonaBackend)
    assert backend.daytona_sandbox is sdk_sandbox
    daytona_class.assert_called_once()


async def test_create_uses_the_configured_daytona_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_SANDBOX_SNAPSHOT", "custom/snapshot:1.0")
    create = MagicMock(return_value=MagicMock(id="sandbox-new"))

    with _daytona_client(create=create):
        backend = await DaytonaProvider().create()

    assert cast(DaytonaBackend, backend).id == "sandbox-new"
    assert create.call_args.kwargs["params"].snapshot == "custom/snapshot:1.0"


async def test_create_refuses_an_open_swe_snapshot() -> None:
    with pytest.raises(ValueError, match="Daytona cannot boot from snapshot 'snap-1'"):
        await DaytonaProvider().create(snapshot_id="snap-1")


async def test_work_dir_comes_from_the_sdk() -> None:
    sdk_sandbox = MagicMock(id="sandbox-1")
    sdk_sandbox.get_work_dir.return_value = "/home/daytona"

    work_dir = await DaytonaProvider().work_dir(DaytonaBackend(sdk_sandbox))

    assert work_dir == "/home/daytona"


async def test_work_dir_is_unknown_for_a_foreign_backend() -> None:
    assert await DaytonaProvider().work_dir(MagicMock()) is None


async def test_missing_api_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DAYTONA_API_KEY environment variable is required"):
        await DaytonaProvider().create()
