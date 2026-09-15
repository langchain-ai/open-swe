from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.sandbox_reset import SandboxResetParams, sandbox_reset


@pytest.mark.asyncio
async def test_sandbox_reset_forwards_public_and_hidden_create_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = {"configurable": {"thread_id": "thread-1", "github_login": "ramonn"}}
    with (
        patch("agent.run_config.get_config", return_value=config),
        patch(
            "agent.sandboxes.lifecycle.reset_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
        ) as reset,
    ):
        result = await sandbox_reset.ainvoke(
            {
                "snapshot_name": "python:latest",
                "cpu_millicores": 500,
                "_internal_runtime": "v2",
                "preserve_memory_on_stop": True,
            }
        )

    assert result == {
        "success": True,
        "old_sandbox_id": "sandbox-old",
        "new_sandbox_id": "sandbox-new",
    }
    reset.assert_awaited_once_with(
        "thread-1",
        {
            "snapshot_name": "python:latest",
            "cpu_millicores": 500,
            "_internal_runtime": "v2",
            "preserve_memory_on_stop": True,
        },
    )


@pytest.mark.asyncio
async def test_sandbox_reset_rejects_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = {"configurable": {"thread_id": "thread-1", "github_login": "someone-else"}}
    with patch("agent.run_config.get_config", return_value=config):
        result = await sandbox_reset.ainvoke({"_internal_runtime": "v2"})

    assert result == {
        "success": False,
        "error": "Only workspace admins can reset sandboxes.",
    }


def test_sandbox_reset_schema_includes_hidden_and_public_options() -> None:
    schema = SandboxResetParams.model_json_schema()

    assert "_internal_runtime" in schema["properties"]
    assert "cpu_millicores" in schema["properties"]
    assert "snapshot_name" in schema["properties"]
    assert schema["additionalProperties"] is True


def test_sandbox_reset_exported() -> None:
    from agent.tools import sandbox_reset as exported

    assert exported is sandbox_reset
