"""LangSmith sandbox backend implementation.

Copied from deepagents-cli to avoid requiring deepagents-cli as a dependency.
"""

from __future__ import annotations

import contextlib
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox

from langsmith.sandbox import Sandbox, SandboxClient, SandboxTemplate


def _get_langsmith_api_key() -> str | None:
    """Get LangSmith API key from environment.

    Checks LANGSMITH_API_KEY first, then falls back to LANGSMITH_API_KEY_PROD
    for LangGraph Cloud deployments where LANGSMITH_API_KEY is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def _get_sandbox_template_config() -> tuple[str | None, str | None]:
    """Get sandbox template configuration from environment.

    Returns:
        Tuple of (template_name, template_image) from environment variables.
        Values are None if not set in environment.
    """
    template_name = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_NAME")
    template_image = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_IMAGE")
    return template_name, template_image


def _create_langsmith_sandbox(
    sandbox_id: str | None = None,
) -> SandboxBackendProtocol:
    """Create or connect to a LangSmith sandbox without automatic cleanup.

    This function directly uses the LangSmithProvider to create/connect to sandboxes
    without the context manager cleanup, allowing sandboxes to persist across
    multiple agent invocations.

    Args:
        sandbox_id: Optional existing sandbox ID to connect to.
                   If None, creates a new sandbox.

    Returns:
        SandboxBackendProtocol instance
    """
    api_key = _get_langsmith_api_key()
    template_name, template_image = _get_sandbox_template_config()

    provider = LangSmithProvider(api_key=api_key)
    return provider.get_or_create(
        sandbox_id=sandbox_id,
        template=template_name,
        template_image=template_image,
    )


class SandboxProvider(ABC):
    """Interface for creating and deleting sandbox backends."""

    @abstractmethod
    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get an existing sandbox, or create one if needed."""
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        *,
        sandbox_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete a sandbox by id."""
        raise NotImplementedError


# Default template configuration
DEFAULT_TEMPLATE_NAME = "deepagents-cli"
DEFAULT_TEMPLATE_IMAGE = "python:3"


class LangSmithBackend(BaseSandbox):
    """LangSmith backend implementation conforming to SandboxBackendProtocol.

    This implementation inherits all file operation methods from BaseSandbox
    and only implements the execute() method using LangSmith's API.
    """

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox
        self._timeout: int = 30 * 60  # 30 mins default

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend."""
        return self._sandbox.name

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command in the sandbox and return ExecuteResponse."""
        result = self._sandbox.run(command, timeout=self._timeout)

        output = result.stdout or ""
        if result.stderr:
            output += "\n" + result.stderr if output else result.stderr

        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=False,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write content using the LangSmith SDK to avoid ARG_MAX.

        BaseSandbox.write() sends the full content in a shell command, which
        can exceed ARG_MAX for large content. This override uses the SDK's
        native write(), which sends content in the HTTP body.
        """
        try:
            self._sandbox.write(file_path, content.encode("utf-8"))
            return WriteResult(path=file_path, files_update=None)
        except Exception as e:
            return WriteResult(error=f"Failed to write file '{file_path}': {e}")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the LangSmith sandbox."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            content = self._sandbox.read(path)
            responses.append(
                FileDownloadResponse(path=path, content=content, error=None)
            )
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the LangSmith sandbox."""
        responses: list[FileUploadResponse] = []
        for path, content in files:
            self._sandbox.write(path, content)
            responses.append(FileUploadResponse(path=path, error=None))
        return responses


class LangSmithProvider(SandboxProvider):
    """LangSmith sandbox provider implementation.

    Manages LangSmith sandbox lifecycle using the LangSmith SDK.
    """

    def __init__(self, api_key: str | None = None) -> None:
        from langsmith import sandbox

        self._api_key = api_key or os.environ.get("LANGSMITH_API_KEY")
        if not self._api_key:
            msg = "LANGSMITH_API_KEY environment variable not set"
            raise ValueError(msg)
        self._client: SandboxClient = sandbox.SandboxClient(api_key=self._api_key)

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        template: str | None = None,
        template_image: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get existing or create new LangSmith sandbox."""
        if kwargs:
            msg = f"Received unsupported arguments: {list(kwargs.keys())}"
            raise TypeError(msg)
        if sandbox_id:
            try:
                sandbox = self._client.get_sandbox(name=sandbox_id)
            except Exception as e:
                msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
                raise RuntimeError(msg) from e
            return LangSmithBackend(sandbox)

        resolved_template_name, resolved_image_name = self._resolve_template(
            template, template_image
        )

        self._ensure_template(resolved_template_name, resolved_image_name)

        try:
            sandbox = self._client.create_sandbox(
                template_name=resolved_template_name, timeout=timeout
            )
        except Exception as e:
            msg = (
                f"Failed to create sandbox from template "
                f"'{resolved_template_name}': {e}"
            )
            raise RuntimeError(msg) from e

        # Verify sandbox is ready by polling
        for _ in range(timeout // 2):
            try:
                result = sandbox.run("echo ready", timeout=5)
                if result.exit_code == 0:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            with contextlib.suppress(Exception):
                self._client.delete_sandbox(sandbox.name)
            msg = f"LangSmith sandbox failed to start within {timeout} seconds"
            raise RuntimeError(msg)

        return LangSmithBackend(sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a LangSmith sandbox."""
        self._client.delete_sandbox(sandbox_id)

    @staticmethod
    def _resolve_template(
        template: SandboxTemplate | str | None,
        template_image: str | None = None,
    ) -> tuple[str, str]:
        """Resolve template name and image from kwargs."""
        resolved_image = template_image or DEFAULT_TEMPLATE_IMAGE
        if template is None:
            return DEFAULT_TEMPLATE_NAME, resolved_image
        if isinstance(template, str):
            return template, resolved_image
        # SandboxTemplate object
        if template_image is None and template.image:
            resolved_image = template.image
        return template.name, resolved_image

    def _ensure_template(
        self,
        template_name: str,
        template_image: str,
    ) -> None:
        """Ensure template exists, creating it if needed."""
        from langsmith.sandbox import ResourceNotFoundError

        try:
            self._client.get_template(template_name)
        except ResourceNotFoundError as e:
            if e.resource_type != "template":
                msg = f"Unexpected resource not found: {e}"
                raise RuntimeError(msg) from e
            try:
                self._client.create_template(name=template_name, image=template_image)
            except Exception as create_err:
                msg = f"Failed to create template '{template_name}': {create_err}"
                raise RuntimeError(msg) from create_err
        except Exception as e:
            msg = f"Failed to check template '{template_name}': {e}"
            raise RuntimeError(msg) from e
