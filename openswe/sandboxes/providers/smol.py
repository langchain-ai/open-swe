"""Smol Machines local and cloud sandbox backend."""

import logging

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
)
from deepagents.backends.sandbox import BaseSandbox
from smol import AsyncMachine, ConnectOptions, ExecOptions, MachineConfig, ResourceSpec, SmolError

from openswe.config import ENV
from openswe.sandboxes.providers.registry import SandboxGoneError

logger = logging.getLogger(__name__)


class SmolSandbox(BaseSandbox):
    def __init__(self, machine: AsyncMachine) -> None:
        self._machine = machine

    @property
    def id(self) -> str:
        return self._machine.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("SmolSandbox is async-only; use aexecute.")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = await self._machine.exec(
            ["sh", "-c", command], ExecOptions(timeout=timeout if timeout is not None else 300)
        )
        stdout = result.stdout_bytes.decode("utf-8", errors="replace")
        stderr = result.stderr_bytes.decode("utf-8", errors="replace")
        return ExecuteResponse(
            output=stdout + stderr,
            exit_code=result.exit_code,
            truncated=result.stdout_truncated or result.stderr_truncated,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError("SmolSandbox is async-only; use aupload_files.")

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses = []
        for path, content in files:
            try:
                await self._machine.write_file(path, content)
            except Exception as exc:
                logger.warning(
                    "Smol sandbox file upload failed", extra={"path": path}, exc_info=True
                )
                responses.append(FileUploadResponse(path=path, error=str(exc)))
            else:
                responses.append(FileUploadResponse(path=path))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError("SmolSandbox is async-only; use adownload_files.")

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            try:
                content = await self._machine.read_file(path)
            except Exception as exc:
                logger.warning(
                    "Smol sandbox file download failed", extra={"path": path}, exc_info=True
                )
                error = "file_not_found" if isinstance(exc, FileNotFoundError) else str(exc)
                responses.append(FileDownloadResponse(path=path, error=error))
            else:
                responses.append(FileDownloadResponse(path=path, content=content))
        return responses


async def create_smol_sandbox(sandbox_id: str | None = None) -> SandboxBackendProtocol:
    """Create or reconnect to a persistent Smol VM without replacing an unreachable one."""
    target = ENV.SMOL_SANDBOX_TARGET.get()
    if target not in {"local", "cloud"}:
        raise ValueError("SMOL_SANDBOX_TARGET must be 'local' or 'cloud'")
    connection = ConnectOptions(target=target)
    if sandbox_id:
        try:
            machine = await AsyncMachine.connect(sandbox_id, connection)
        except SmolError as exc:
            if exc.code == "NOT_FOUND":
                raise SandboxGoneError(f"Smol sandbox {sandbox_id!r} was deleted") from exc
            raise
        state = await machine.state()
        if state == "paused":
            await machine.resume()
        elif state in {"stopped", "created"}:
            await machine.start()
        elif state == "deleted":
            raise SandboxGoneError(f"Smol sandbox {sandbox_id!r} was deleted")
        else:
            await machine.wait_until_ready()
    else:
        image = ENV.SMOL_SANDBOX_IMAGE.optional()
        if not image:
            raise ValueError(
                "SMOL_SANDBOX_IMAGE must name an image with sh, git, gh, python3 and GNU grep"
            )
        machine = await AsyncMachine.create(
            MachineConfig(
                image=image,
                command=["sleep", "infinity"],
                persistent=True,
                network=True,
                resources=ResourceSpec(cpus=2, memory_mb=2048),
            ),
            connection,
        )
        try:
            setup = ENV.SMOL_SANDBOX_SETUP_COMMAND.optional()
            if setup:
                result = await machine.exec(["sh", "-c", setup], ExecOptions(timeout=300))
                if result.exit_code != 0:
                    raise RuntimeError(f"Smol sandbox setup failed: {result.stderr}")
            tools = await machine.exec(
                [
                    "sh",
                    "-c",
                    "command -v git && command -v gh && command -v python3 && "
                    "printf x | grep -HnFZ -e x >/dev/null 2>&1",
                ]
            )
            if tools.exit_code != 0:
                raise ValueError(
                    "SMOL_SANDBOX_IMAGE must include sh, git, gh, python3 and GNU grep"
                )
        except Exception:
            try:
                await machine.delete()
            except Exception:
                logger.warning("Failed to delete Smol sandbox after setup failed", exc_info=True)
            raise
    return SmolSandbox(machine)
