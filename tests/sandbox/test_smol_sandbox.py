"""Regression coverage for existing Smol sandbox reconnection."""

from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from smol import AsyncMachine, SmolError

from openswe.sandboxes.providers.registry import SandboxGoneError
from openswe.sandboxes.providers.smol import SmolSandbox, create_smol_sandbox


@pytest.mark.asyncio
async def test_missing_smol_sandbox_is_not_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMOL_SANDBOX_TARGET", "cloud")
    with (
        patch.object(AsyncMachine, "connect", new_callable=AsyncMock) as connect,
        patch.object(AsyncMachine, "create", new_callable=AsyncMock) as create,
    ):
        connect.side_effect = SmolError("NOT_FOUND", "machine was deleted")
        with pytest.raises(SandboxGoneError, match="machine-123"):
            await create_smol_sandbox("machine-123")
        create.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_smol_file_returns_standard_not_found_error() -> None:
    machine = AsyncMock(spec=AsyncMachine)
    machine.read_file.side_effect = SmolError("NOT_FOUND", "guest file does not exist")
    sandbox = SmolSandbox(cast(AsyncMachine, machine))

    response = await sandbox.adownload_files(["/workspace/gone.txt"])

    assert response[0].error == "file_not_found"
    assert response[0].content is None
