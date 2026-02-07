"""Protocol definitions for sandbox backends.

This module defines the SandboxBackendProtocol that sandbox implementations
must follow. Copied from deepagents to reduce coupling.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExecuteResponse:
    """Result of code execution.

    Simplified schema optimized for LLM consumption.
    """

    output: str
    """Combined stdout and stderr output of the executed command."""

    exit_code: int | None = None
    """The process exit code. 0 indicates success, non-zero indicates failure."""

    truncated: bool = False
    """Whether the output was truncated due to backend limitations."""


@runtime_checkable
class SandboxBackendProtocol(Protocol):
    """Protocol for sandbox backends that support shell command execution.

    Designed for backends running in isolated environments (containers, VMs,
    remote hosts).

    This is a minimal protocol for type checking - actual implementations
    come from deepagents.
    """

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend instance."""
        ...

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command in the sandbox.

        Args:
            command: Full shell command string to execute.

        Returns:
            ExecuteResponse with combined output, exit code, and truncation flag.
        """
        ...

    async def aexecute(self, command: str) -> ExecuteResponse:
        """Async version of execute."""
        ...

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read file content with line numbers."""
        ...

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Async version of read."""
        ...

    def write(self, file_path: str, content: str) -> "WriteResult":
        """Write content to a new file."""
        ...

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> "EditResult":
        """Perform exact string replacements in an existing file."""
        ...


@dataclass
class WriteResult:
    """Result from backend write operations."""

    error: str | None = None
    path: str | None = None


@dataclass
class EditResult:
    """Result from backend edit operations."""

    error: str | None = None
    path: str | None = None
    occurrences: int | None = None
