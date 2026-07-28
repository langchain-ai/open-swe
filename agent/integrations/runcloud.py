import os
import shlex
from pathlib import PurePosixPath

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from runcloud import Client, Sandbox

DEFAULT_RUN_CLOUD_IMAGE = "runcloud/agent-base"
RUN_CLOUD_IMAGE_ENV = "RUN_CLOUD_IMAGE"


class RunCloudSandbox(BaseSandbox):
    """Deep Agents backend backed by a Run Cloud microVM."""

    def __init__(self, sandbox: Sandbox):
        self._sandbox = sandbox

    @property
    def id(self) -> str:
        return self._sandbox.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = self._sandbox.exec(command, timeout=timeout)
        return ExecuteResponse(
            output=result.stdout + result.stderr,
            exit_code=result.exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                parent = str(PurePosixPath(path).parent)
                mkdir = self._sandbox.exec(f"mkdir -p {shlex.quote(parent)}")
                if mkdir.exit_code != 0:
                    responses.append(
                        FileUploadResponse(path=path, error=mkdir.stderr or mkdir.stdout)
                    )
                    continue
                self._sandbox.write_file(path, content)
                responses.append(FileUploadResponse(path=path))
            except Exception as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                responses.append(
                    FileDownloadResponse(path=path, content=self._sandbox.read_file(path))
                )
            except Exception as exc:
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses


def create_runcloud_sandbox(sandbox_id: str | None = None) -> RunCloudSandbox:
    """Create or reconnect to a Run Cloud sandbox."""
    client = Client()

    if sandbox_id:
        sandbox = client.get(sandbox_id)
    else:
        image = os.getenv(RUN_CLOUD_IMAGE_ENV, DEFAULT_RUN_CLOUD_IMAGE).strip()
        if not image:
            raise ValueError(f"{RUN_CLOUD_IMAGE_ENV} must not be empty")
        sandbox = client.create(image=image)

    return RunCloudSandbox(sandbox)
