from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import modal
import pytest
from langchain_modal import ModalSandbox

from agent.integrations.modal import ModalProvider
from agent.utils.sandbox import SandboxGoneError


def _from_id(**kwargs: object):
    return patch("modal.Sandbox.from_id", MagicMock(aio=AsyncMock(**kwargs)))


async def test_connect_reconnects_by_id() -> None:
    sdk_sandbox = MagicMock(object_id="sb-1")

    with _from_id(return_value=sdk_sandbox):
        backend = await ModalProvider().connect("sb-1")

    assert cast(ModalSandbox, backend).id == "sb-1"


async def test_connect_reports_a_deleted_sandbox_as_gone() -> None:
    with _from_id(side_effect=modal.exception.NotFoundError("no such sandbox")):
        with pytest.raises(SandboxGoneError, match="sb-gone"):
            await ModalProvider().connect("sb-gone")


async def test_connect_keeps_other_failures_untyped() -> None:
    with _from_id(side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError) as excinfo:
            await ModalProvider().connect("sb-1")

    assert not isinstance(excinfo.value, SandboxGoneError)


async def test_create_looks_up_the_configured_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODAL_APP_NAME", "open-swe-modal")
    app = MagicMock()
    lookup = AsyncMock(return_value=app)
    create = AsyncMock(return_value=MagicMock(object_id="sb-new"))

    with (
        patch("modal.App.lookup", MagicMock(aio=lookup)),
        patch("modal.Sandbox.create", MagicMock(aio=create)),
    ):
        backend = await ModalProvider().create()

    assert cast(ModalSandbox, backend).id == "sb-new"
    lookup.assert_awaited_once_with("open-swe-modal")
    create.assert_awaited_once_with(app=app)


async def test_create_refuses_an_open_swe_snapshot() -> None:
    with pytest.raises(ValueError, match="Modal cannot boot from snapshot 'snap-1'"):
        await ModalProvider().create(snapshot_id="snap-1")


async def test_work_dir_is_left_to_the_shell() -> None:
    assert await ModalProvider().work_dir(MagicMock()) is None
