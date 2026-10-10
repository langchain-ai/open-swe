"""Regression coverage for existing Smol sandbox reconnection."""

from unittest.mock import AsyncMock, patch

import pytest
from smol import AsyncMachine, SmolError

from openswe.sandboxes.providers.registry import SandboxGoneError
from openswe.sandboxes.providers.smol import create_smol_sandbox


@pytest.mark.asyncio
async def test_missing_smol_sandbox_is_not_silently_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOL_SANDBOX_TARGET", "cloud")
    with (
        patch.object(AsyncMachine, "connect", new_callable=AsyncMock) as connect,
        patch.object(AsyncMachine, "create", new_callable=AsyncMock) as create,
    ):
        connect.side_effect = SmolError("NOT_FOUND", "machine was deleted")
        with pytest.raises(SandboxGoneError, match="machine-123"):
            await create_smol_sandbox("machine-123")
        create.assert_not_awaited()
