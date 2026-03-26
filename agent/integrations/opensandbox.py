"""OpenSandbox backend implementation for Open SWE Agent.

Uses the OpenSandbox Python SDK (sync) to provide sandbox isolation.
Requires: pip install opensandbox

Environment variables:
    OPEN_SANDBOX_API_KEY: API key for OpenSandbox server authentication.
    OPEN_SANDBOX_DOMAIN: OpenSandbox server domain (default: localhost:8080).
    OPEN_SANDBOX_PROTOCOL: HTTP protocol, "http" or "https" (default: http).
    OPENSANDBOX_IMAGE: Container image to use (default: python:3.12).
    OPENSANDBOX_TIMEOUT_SECONDS: Sandbox TTL in seconds (default: 3600).
    OPENSANDBOX_USE_SERVER_PROXY: Route execd traffic through the server (default: true).
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.models.execd import RunCommandOpts
from opensandbox.sync.sandbox import SandboxSync

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_IMAGE = "python:3.12"
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_COMMAND_TIMEOUT = 300  # 5 minutes


class OpenSandboxBackend(BaseSandbox):
    """OpenSandbox backend conforming to SandboxBackendProtocol.

    Inherits file operation methods from BaseSandbox (which delegates to execute()).
    Overrides write/download_files/upload_files with native SDK calls for efficiency.
    """

    def __init__(self, sandbox: SandboxSync) -> None:
        self._sandbox = sandbox
        self._default_timeout: int = DEFAULT_COMMAND_TIMEOUT

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend."""
        return self._sandbox.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command in the sandbox and return ExecuteResponse."""
        effective_timeout = timeout if timeout is not None else self._default_timeout
        opts = RunCommandOpts(timeout=timedelta(seconds=effective_timeout))
        result = self._sandbox.commands.run(command, opts=opts)

        # Combine stdout and stderr
        stdout_text = "".join(msg.text for msg in result.logs.stdout) if result.logs.stdout else ""
        stderr_text = "".join(msg.text for msg in result.logs.stderr) if result.logs.stderr else ""

        output = stdout_text
        if stderr_text:
            output = f"{output}\n{stderr_text}" if output else stderr_text

        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=False,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write content using the OpenSandbox SDK to avoid ARG_MAX issues."""
        try:
            self._sandbox.files.write_file(file_path, content)
            return WriteResult(path=file_path, files_update=None)
        except Exception as e:
            return WriteResult(error=f"Failed to write file '{file_path}': {e}")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the sandbox."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                content = self._sandbox.files.read_file(path)
                responses.append(
                    FileDownloadResponse(path=path, content=content.encode("utf-8"), error=None)
                )
            except Exception:
                responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the sandbox."""
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                self._sandbox.files.write_file(path, content)
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
        return responses


def _get_connection_config() -> ConnectionConfigSync:
    """Build ConnectionConfigSync from environment variables."""
    api_key = os.getenv("OPEN_SANDBOX_API_KEY", "")
    domain = os.getenv("OPEN_SANDBOX_DOMAIN", "localhost:8080")
    protocol = os.getenv("OPEN_SANDBOX_PROTOCOL", "http")
    use_server_proxy = os.getenv("OPENSANDBOX_USE_SERVER_PROXY", "true").lower() in (
        "true",
        "1",
        "yes",
    )

    if not api_key:
        raise ValueError(
            "OPEN_SANDBOX_API_KEY environment variable is required. "
            "Set it to your OpenSandbox server API key."
        )

    return ConnectionConfigSync(
        api_key=api_key,
        domain=domain,
        protocol=protocol,
        use_server_proxy=use_server_proxy,
    )


def create_opensandbox_sandbox(
    sandbox_id: str | None = None,
) -> SandboxBackendProtocol:
    """Create or connect to an OpenSandbox sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to connect to.
                   If None, creates a new sandbox.

    Returns:
        SandboxBackendProtocol instance
    """
    config = _get_connection_config()

    if sandbox_id:
        logger.info("Connecting to existing OpenSandbox: %s", sandbox_id)
        sandbox = SandboxSync.connect(sandbox_id, connection_config=config)
        return OpenSandboxBackend(sandbox)

    image = os.getenv("OPENSANDBOX_IMAGE", DEFAULT_IMAGE)
    timeout_seconds = int(os.getenv("OPENSANDBOX_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))

    logger.info("Creating new OpenSandbox with image: %s (timeout: %ds)", image, timeout_seconds)
    sandbox = SandboxSync.create(
        image,
        timeout=timedelta(seconds=timeout_seconds),
        connection_config=config,
    )
    logger.info("OpenSandbox created: %s", sandbox.id)

    return OpenSandboxBackend(sandbox)
