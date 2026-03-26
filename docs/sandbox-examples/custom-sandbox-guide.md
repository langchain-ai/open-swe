# Custom Sandbox Integration Guide

This guide explains how to implement a custom sandbox provider for Open SWE. If the built-in sandbox providers (LangSmith, Modal, Daytona, Runloop, Docker, Local) don't meet your needs, you can create your own.

## Overview

A sandbox provider is responsible for:

1. **Creating** isolated execution environments
2. **Executing** shell commands within those environments
3. **Managing** file operations (read, write, list, search)
4. **Cleaning up** resources when no longer needed

## The SandboxBackendProtocol

All sandbox backends must implement `SandboxBackendProtocol` from `deepagents.backends.protocol`. The protocol requires:

```python
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    WriteResult,
)

class SandboxBackendProtocol(Protocol):
    """Protocol that all sandbox backends must implement."""

    @property
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        ...

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command in the sandbox.

        Args:
            command: The shell command to execute.
            timeout: Optional timeout in seconds.

        Returns:
            ExecuteResponse with output, exit_code, and truncated flag.
        """
        ...

    def ls(self, path: str) -> list[dict]:
        """List directory contents."""
        ...

    def read(self, path: str) -> str:
        """Read file contents."""
        ...

    def write(self, path: str, content: str) -> WriteResult:
        """Write content to a file."""
        ...

    def edit(self, path: str, edits: list[dict]) -> WriteResult:
        """Apply edits to a file."""
        ...

    def glob(self, pattern: str, path: str = ".") -> list[str]:
        """Find files matching a glob pattern."""
        ...

    def grep(self, pattern: str, path: str = ".") -> list[dict]:
        """Search for pattern in files."""
        ...
```

## Using BaseSandbox

The easiest way to implement a custom sandbox is to extend `BaseSandbox` from `deepagents.backends.sandbox`. This class provides default implementations for all file operations by delegating to `execute()`, so you only need to implement the shell execution layer.

```python
from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse

class MySandbox(BaseSandbox):
    """Custom sandbox implementation."""

    def __init__(self, connection):
        self._connection = connection

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox."""
        return self._connection.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a command in the sandbox."""
        # Your implementation here
        result = self._connection.run(command, timeout=timeout or 300)

        return ExecuteResponse(
            output=result.stdout + result.stderr,
            exit_code=result.exit_code,
            truncated=False,
        )
```

## Step-by-Step Implementation

### Step 1: Create the Integration File

Create a new file at `agent/integrations/my_provider.py`:

```python
"""My custom sandbox provider implementation.

This module provides a [description of your sandbox provider].

Configuration via environment variables:
    - MY_PROVIDER_API_KEY: API key for authentication (if needed)
    - MY_PROVIDER_REGION: Region for sandbox creation (if applicable)
"""

from __future__ import annotations

import os
import logging
from typing import Any

from deepagents.backends.protocol import ExecuteResponse, WriteResult
from deepagents.backends.sandbox import BaseSandbox

logger = logging.getLogger(__name__)


class MySandboxBackend(BaseSandbox):
    """Custom sandbox backend implementing SandboxBackendProtocol.

    [Detailed description of your sandbox implementation.]

    Attributes:
        _connection: The underlying connection/client to your sandbox service.
        _config: Configuration dictionary.
    """

    def __init__(
        self,
        connection: Any,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the sandbox backend.

        Args:
            connection: Your sandbox service connection/client.
            config: Optional configuration dictionary.
        """
        self._connection = connection
        self._config = config or {}
        self._default_timeout = self._config.get("timeout", 300)

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend."""
        return self._connection.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a command in the sandbox.

        Args:
            command: Full shell command string to execute.
            timeout: Maximum time in seconds to wait for completion.

        Returns:
            ExecuteResponse with output, exit code, and truncation flag.
        """
        effective_timeout = timeout if timeout is not None else self._default_timeout

        try:
            # Execute the command using your sandbox service
            result = self._connection.execute(command, timeout=effective_timeout)

            # Combine stdout and stderr
            output = result.stdout or ""
            if result.stderr:
                output += "\n" + result.stderr if output else result.stderr

            return ExecuteResponse(
                output=output,
                exit_code=result.exit_code,
                truncated=False,
            )

        except Exception as e:
            error_msg = f"Command execution failed: {e}"
            logger.error(error_msg)
            return ExecuteResponse(
                output=error_msg,
                exit_code=1,
                truncated=False,
            )

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write content to a file in the sandbox.

        Override this if your sandbox has a more efficient way to write files
        than using shell commands (which can hit ARG_MAX limits).

        Args:
            file_path: Absolute path where the file should be written.
            content: String content to write.

        Returns:
            WriteResult with path and any error information.
        """
        # Option 1: Use BaseSandbox default (shell commands)
        return super().write(file_path, content)

        # Option 2: Use your sandbox's native file write API
        # try:
        #     self._connection.write_file(file_path, content)
        #     return WriteResult(path=file_path, files_update=None)
        # except Exception as e:
        #     return WriteResult(error=f"Failed to write file: {e}")


class MySandboxProvider:
    """Provider for creating and managing sandbox instances."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the sandbox provider.

        Args:
            config: Optional configuration dictionary.
        """
        self._config = config or {}
        self._api_key = os.getenv("MY_PROVIDER_API_KEY")

        if not self._api_key:
            raise ValueError("MY_PROVIDER_API_KEY environment variable is required")

        # Initialize your sandbox service client
        self._client = self._create_client()

    def _create_client(self) -> Any:
        """Create and return the sandbox service client."""
        # Your client initialization logic here
        pass

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> MySandboxBackend:
        """Get an existing sandbox or create a new one.

        Args:
            sandbox_id: Optional existing sandbox ID to reconnect to.
            **kwargs: Additional provider-specific arguments.

        Returns:
            MySandboxBackend instance.
        """
        if sandbox_id:
            # Reconnect to existing sandbox
            connection = self._client.get_sandbox(sandbox_id)
        else:
            # Create new sandbox
            connection = self._client.create_sandbox()

        return MySandboxBackend(connection, self._config)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a sandbox by ID.

        Args:
            sandbox_id: ID of the sandbox to delete.
            **kwargs: Additional provider-specific arguments.
        """
        self._client.delete_sandbox(sandbox_id)


def create_my_provider_sandbox(
    sandbox_id: str | None = None,
) -> MySandboxBackend:
    """Factory function to create or reconnect to a sandbox.

    This function is registered in the SANDBOX_FACTORIES dictionary.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.

    Returns:
        MySandboxBackend instance implementing SandboxBackendProtocol.
    """
    provider = MySandboxProvider()
    backend = provider.get_or_create(sandbox_id=sandbox_id)
    _update_thread_sandbox_metadata(backend.id)
    return backend


def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Update thread metadata with sandbox_id for persistence."""
    try:
        import asyncio
        from langgraph.config import get_config
        from langgraph_sdk import get_client

        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return
        client = get_client()

        async def _update() -> None:
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": sandbox_id},
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_update())
        else:
            loop.create_task(_update())
    except Exception:
        pass  # Best-effort: ignore failures
```

### Step 2: Register the Factory

Update `agent/utils/sandbox.py` to register your factory:

```python
from agent.integrations.my_provider import create_my_provider_sandbox

SANDBOX_FACTORIES = {
    # ... existing factories ...
    "my_provider": create_my_provider_sandbox,
}
```

### Step 3: Update Exports (Optional)

Update `agent/integrations/__init__.py` if you want to export your classes:

```python
from agent.integrations.my_provider import MySandboxBackend, MySandboxProvider

__all__ = [
    # ... existing exports ...
    "MySandboxBackend",
    "MySandboxProvider",
]
```

### Step 4: Use Your Sandbox

Set the environment variable and use your sandbox:

```bash
export SANDBOX_TYPE=my_provider
export MY_PROVIDER_API_KEY=your-api-key
```

## Reference Implementation: Docker Sandbox

The Docker sandbox implementation (`agent/integrations/docker.py`) is a complete reference that demonstrates:

1. **BaseSandbox extension**: Only implements `execute()` and overrides `write()` for efficiency
2. **Provider class**: Manages container lifecycle
3. **Configuration from environment**: Loads settings from environment variables
4. **Reconnection support**: Can reconnect to existing containers
5. **Resource management**: Memory and CPU limits
6. **Security options**: Network isolation and security profiles
7. **Error handling**: Graceful error handling with informative messages
8. **Cleanup functions**: Helper functions for managing containers

## Best Practices

### 1. Handle Timeouts Gracefully

```python
def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
    effective_timeout = timeout or self._default_timeout
    try:
        result = self._connection.run(command, timeout=effective_timeout)
        return ExecuteResponse(output=result.output, exit_code=result.exit_code, truncated=False)
    except TimeoutError:
        return ExecuteResponse(
            output=f"Command timed out after {effective_timeout} seconds",
            exit_code=124,  # Standard timeout exit code
            truncated=False,
        )
```

### 2. Override `write()` for Large Files

The default `BaseSandbox.write()` uses shell commands which can hit `ARG_MAX` limits. Override it to use your sandbox's native file API:

```python
def write(self, file_path: str, content: str) -> WriteResult:
    try:
        self._connection.write_file(file_path, content)
        return WriteResult(path=file_path, files_update=None)
    except Exception as e:
        return WriteResult(error=f"Failed to write: {e}")
```

### 3. Implement Cleanup

Always provide a way to clean up resources:

```python
class MySandboxProvider:
    def delete(self, *, sandbox_id: str) -> None:
        """Delete a sandbox and release resources."""
        try:
            self._client.delete_sandbox(sandbox_id)
        except Exception as e:
            logger.warning(f"Failed to delete sandbox {sandbox_id}: {e}")
```

### 4. Support Reconnection

Allow reconnecting to existing sandboxes for persistence across agent invocations:

```python
def get_or_create(self, *, sandbox_id: str | None = None) -> MySandboxBackend:
    if sandbox_id:
        # Reconnect to existing
        connection = self._client.get_sandbox(sandbox_id)
    else:
        # Create new
        connection = self._client.create_sandbox()
    return MySandboxBackend(connection)
```

### 5. Validate Configuration

Check required configuration at initialization time:

```python
def __init__(self, config: dict | None = None) -> None:
    self._api_key = os.getenv("MY_PROVIDER_API_KEY")
    if not self._api_key:
        raise ValueError("MY_PROVIDER_API_KEY environment variable is required")
```

### 6. Use Meaningful IDs

Return stable, meaningful IDs that can be used for reconnection:

```python
@property
def id(self) -> str:
    return self._connection.id  # Stable ID from the sandbox service
```

## Testing Your Sandbox

Create a test file to verify your implementation:

```python
# tests/test_my_provider_sandbox.py

import pytest
from agent.integrations.my_provider import MySandboxBackend, MySandboxProvider


def test_sandbox_create_and_execute():
    """Test basic sandbox creation and command execution."""
    provider = MySandboxProvider()
    sandbox = provider.get_or_create()

    # Test basic command
    result = sandbox.execute("echo 'Hello, World!'")
    assert result.exit_code == 0
    assert "Hello, World!" in result.output

    # Test file operations
    sandbox.write("/tmp/test.txt", "Test content")
    content = sandbox.read("/tmp/test.txt")
    assert content == "Test content"

    # Cleanup
    provider.delete(sandbox_id=sandbox.id)


def test_sandbox_reconnect():
    """Test reconnecting to an existing sandbox."""
    provider = MySandboxProvider()

    # Create initial sandbox
    sandbox1 = provider.get_or_create()
    sandbox_id = sandbox1.id

    # Write a file
    sandbox1.write("/tmp/persistent.txt", "Persistent data")

    # Reconnect
    sandbox2 = provider.get_or_create(sandbox_id=sandbox_id)

    # Verify persistence
    content = sandbox2.read("/tmp/persistent.txt")
    assert content == "Persistent data"

    # Cleanup
    provider.delete(sandbox_id=sandbox_id)


def test_sandbox_timeout():
    """Test command timeout handling."""
    provider = MySandboxProvider()
    sandbox = provider.get_or_create()

    # Test timeout
    result = sandbox.execute("sleep 100", timeout=1)
    assert result.exit_code != 0 or "timeout" in result.output.lower()

    provider.delete(sandbox_id=sandbox.id)
```

## Integration with Open SWE

Once implemented, your sandbox integrates seamlessly with Open SWE:

1. Set the environment variable:
   ```bash
   export SANDBOX_TYPE=my_provider
   ```

2. Configure any required credentials:
   ```bash
   export MY_PROVIDER_API_KEY=your-key
   ```

3. Start the Open SWE agent:
   ```bash
   langgraph dev
   ```

The agent will automatically use your sandbox for all code execution tasks.