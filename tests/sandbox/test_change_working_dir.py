import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import ExecuteResponse, ReadResult

from agent.sandboxes.state import SANDBOX_BACKENDS, SandboxBackendProxy

change_working_dir_module = importlib.import_module("agent.tools.change_working_dir")


class FakeBackend:
    id = "sandbox-1"

    def __init__(self, instructions: str | None = None) -> None:
        self.commands: list[str] = []
        self.instructions = instructions

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.commands.append(command)
        if command.startswith("realpath"):
            return ExecuteResponse(output="/repo\n", exit_code=0)
        return ExecuteResponse(output="/repo\n", exit_code=0)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if self.instructions is None:
            return ReadResult(error="File not found")
        return ReadResult(file_data={"content": self.instructions, "encoding": "utf-8"})


@pytest.mark.asyncio
async def test_switch_loads_instructions_and_persists_for_commands(monkeypatch) -> None:
    backend = FakeBackend("follow repo rules")
    proxy = SandboxBackendProxy(backend, thread_id="thread")
    SANDBOX_BACKENDS["thread"] = proxy
    monkeypatch.setattr(
        change_working_dir_module.RunConfig,
        "from_runtime",
        lambda: SimpleNamespace(thread_id="thread"),
    )
    try:
        result = await change_working_dir_module.change_working_dir("/repo")
        assert "follow repo rules" in result
        assert proxy.working_directory == "/repo"
        await proxy.aexecute("pwd")
        assert backend.commands[-1] == "cd -- /repo && pwd"
        replacement = FakeBackend()
        proxy.replace_backend(replacement)
        await proxy.aexecute("pwd")
        assert replacement.commands[-1] == "cd -- /repo && pwd"
        offload = await proxy.aexecute_with_offload("pwd", "/tmp/capture", max_inline_bytes=100)
        assert offload.response.output == "/repo\n"
        assert replacement.commands[-1] == "cd -- /repo && pwd"
    finally:
        SANDBOX_BACKENDS.pop("thread", None)


@pytest.mark.asyncio
async def test_invalid_directory_does_not_change_existing_directory(monkeypatch) -> None:
    backend = FakeBackend()
    proxy = SandboxBackendProxy(backend, thread_id="thread")
    proxy.change_working_directory("/original")
    SANDBOX_BACKENDS["thread"] = proxy
    monkeypatch.setattr(
        change_working_dir_module.RunConfig,
        "from_runtime",
        lambda: SimpleNamespace(thread_id="thread"),
    )
    backend.aread = AsyncMock(return_value=ReadResult(error="Permission denied"))
    try:
        with pytest.raises(RuntimeError, match="Permission denied"):
            await change_working_dir_module.change_working_dir("/repo")
        assert proxy.working_directory == "/original"
    finally:
        SANDBOX_BACKENDS.pop("thread", None)
