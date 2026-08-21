from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from e2b import NotFoundException
from langchain_e2b import E2BSandbox

from agent.integrations.e2b import E2B_WORK_DIR, E2BProvider
from agent.utils.sandbox import SandboxGoneError


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "api-key")
    monkeypatch.delenv("E2B_TEMPLATE", raising=False)


def _sdk_sandbox() -> MagicMock:
    return MagicMock(sandbox_id="sandbox-1")


async def test_create_uses_the_default_timeout() -> None:
    create = MagicMock(return_value=_sdk_sandbox())

    with patch("agent.integrations.e2b.Sandbox.create", create):
        backend = await E2BProvider().create()

    assert cast(E2BSandbox, backend).id == "sandbox-1"
    assert create.call_args.kwargs == {"timeout": 3600, "api_key": "api-key"}


async def test_create_uses_the_configured_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_TEMPLATE", "open-swe-template")
    create = MagicMock(return_value=_sdk_sandbox())

    with patch("agent.integrations.e2b.Sandbox.create", create):
        await E2BProvider().create()

    assert create.call_args.kwargs == {
        "template": "open-swe-template",
        "timeout": 3600,
        "api_key": "api-key",
    }


async def test_create_refuses_an_open_swe_snapshot() -> None:
    with pytest.raises(ValueError, match="E2B cannot boot from snapshot 'snap-1'"):
        await E2BProvider().create(snapshot_id="snap-1")


async def test_empty_template_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_TEMPLATE", "  ")

    with pytest.raises(ValueError, match="E2B_TEMPLATE must not be empty"):
        await E2BProvider().create()


async def test_connect_reconnects_by_id() -> None:
    connect = MagicMock(return_value=MagicMock(sandbox_id="sbx_existing"))

    with patch("agent.integrations.e2b.Sandbox.connect", connect):
        backend = await E2BProvider().connect("sbx_existing")

    assert cast(E2BSandbox, backend).id == "sbx_existing"
    assert connect.call_args.args == ("sbx_existing",)
    assert connect.call_args.kwargs == {"timeout": 3600, "api_key": "api-key"}


async def test_connect_reports_a_deleted_sandbox_as_gone() -> None:
    connect = MagicMock(side_effect=NotFoundException("no such sandbox"))

    with patch("agent.integrations.e2b.Sandbox.connect", connect):
        with pytest.raises(SandboxGoneError, match="sbx_gone"):
            await E2BProvider().connect("sbx_gone")


async def test_connect_keeps_other_failures_untyped() -> None:
    connect = MagicMock(side_effect=RuntimeError("boom"))

    with patch("agent.integrations.e2b.Sandbox.connect", connect):
        with pytest.raises(RuntimeError) as excinfo:
            await E2BProvider().connect("sbx_1")

    assert not isinstance(excinfo.value, SandboxGoneError)


async def test_work_dir_matches_the_directory_commands_run_in() -> None:
    with patch("agent.integrations.e2b.Sandbox.create", MagicMock(return_value=_sdk_sandbox())):
        backend = await E2BProvider().create()

    assert await E2BProvider().work_dir(backend) == E2B_WORK_DIR
    assert cast(E2BSandbox, backend)._workdir == E2B_WORK_DIR


async def test_missing_api_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2B_API_KEY", raising=False)

    with pytest.raises(ValueError, match="E2B_API_KEY environment variable is required"):
        await E2BProvider().create()
