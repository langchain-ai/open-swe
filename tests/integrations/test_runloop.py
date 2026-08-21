from contextlib import AbstractContextManager
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest
from langchain_runloop import RunloopSandbox
from runloop_api_client import NotFoundError

from agent.integrations.runloop import RunloopProvider
from agent.utils.sandbox import SandboxGoneError


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNLOOP_API_KEY", "api-key")


def _runloop_client(**devbox_attrs: Any) -> AbstractContextManager[MagicMock]:
    client = MagicMock()
    client.devboxes.configure_mock(**devbox_attrs)
    return cast(
        AbstractContextManager[MagicMock],
        patch("agent.integrations.runloop.Client", return_value=client),
    )


def _not_found() -> NotFoundError:
    request = httpx.Request("GET", "https://api.runloop.ai/v1/devboxes/dbx-gone")
    return NotFoundError(
        "no such devbox",
        response=httpx.Response(404, request=request),
        body=None,
    )


async def test_connect_reconnects_by_id() -> None:
    devbox = MagicMock(id="dbx-1")

    with _runloop_client(retrieve=MagicMock(return_value=devbox)):
        backend = await RunloopProvider().connect("dbx-1")

    assert cast(RunloopSandbox, backend).id == "dbx-1"


async def test_connect_reports_a_deleted_devbox_as_gone() -> None:
    with _runloop_client(retrieve=MagicMock(side_effect=_not_found())):
        with pytest.raises(SandboxGoneError, match="dbx-gone"):
            await RunloopProvider().connect("dbx-gone")


async def test_connect_keeps_other_failures_untyped() -> None:
    with _runloop_client(retrieve=MagicMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError) as excinfo:
            await RunloopProvider().connect("dbx-1")

    assert not isinstance(excinfo.value, SandboxGoneError)


async def test_create_provisions_a_devbox() -> None:
    with _runloop_client(create=MagicMock(return_value=MagicMock(id="dbx-new"))):
        backend = await RunloopProvider().create()

    assert cast(RunloopSandbox, backend).id == "dbx-new"


async def test_create_refuses_an_open_swe_snapshot() -> None:
    with pytest.raises(ValueError, match="Runloop cannot boot from snapshot 'snap-1'"):
        await RunloopProvider().create(snapshot_id="snap-1")


async def test_work_dir_is_left_to_the_shell() -> None:
    assert await RunloopProvider().work_dir(MagicMock()) is None


async def test_missing_api_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNLOOP_API_KEY", raising=False)

    with pytest.raises(ValueError, match="RUNLOOP_API_KEY environment variable is required"):
        await RunloopProvider().create()
