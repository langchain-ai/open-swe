"""Docker sandbox backend implementation.

This module provides a Docker-based sandbox for running code in isolated containers.
It's useful for:
- Local development with isolation (unlike the 'local' sandbox which has no isolation)
- Self-hosted deployments where you control the Docker host
- Testing and CI/CD pipelines
- Environments where cloud sandbox services (Modal, Daytona, Runloop, LangSmith)
  are not available or not desired

Requirements:
    - Docker must be installed and running on the host
    - The 'docker' Python package: pip install docker
    - Appropriate permissions to create and manage containers

Configuration via environment variables:
    - DOCKER_SANDBOX_IMAGE: Base image for containers (default: python:3.11-slim)
    - DOCKER_SANDBOX_WORK_DIR: Working directory in container (default: /workspace)
    - DOCKER_SANDBOX_MEMORY: Memory limit in bytes (default: 2GB = 2147483648)
    - DOCKER_SANDBOX_CPU_PERIOD: CPU period for limiting (default: 100000)
    - DOCKER_SANDBOX_CPU_QUOTA: CPU quota for limiting (default: 100000 = 1 CPU)
    - DOCKER_SANDBOX_TIMEOUT: Default command timeout in seconds (default: 300)
    - DOCKER_SANDBOX_NETWORK: Network mode (default: bridge)
    - DOCKER_SANDBOX_VOLUMES: JSON dict of volume mounts (optional)

Example usage:
    # Set environment variables
    export SANDBOX_TYPE=docker
    export DOCKER_SANDBOX_IMAGE=python:3.11-slim
    export DOCKER_SANDBOX_MEMORY=4294967296  # 4GB

    # The sandbox will be automatically used when SANDBOX_TYPE=docker
    from agent.utils.sandbox import create_sandbox
    sandbox = create_sandbox()
    result = sandbox.execute("ls -la")
    print(result.output)

Security considerations:
    - Containers provide isolation but are not as secure as VMs
    - Consider using Docker security options (no_new_privileges, read-only root, etc.)
    - Network isolation: use 'none' network if network access is not needed
    - Resource limits: always set memory and CPU limits to prevent runaway processes
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_WORK_DIR = "/workspace"
DEFAULT_MEMORY = 2 * 1024 * 1024 * 1024  # 2GB
DEFAULT_CPU_PERIOD = 100000  # 100ms
DEFAULT_CPU_QUOTA = 100000  # 1 CPU (period == quota = 100% of 1 CPU)
DEFAULT_TIMEOUT = 300  # 5 minutes
DEFAULT_NETWORK = "bridge"
DEFAULT_CONTAINER_PREFIX = "open-swe-sandbox"


def _get_docker_client():
    """Get a Docker client instance.

    Returns:
        Docker client instance.

    Raises:
        ImportError: If the 'docker' package is not installed.
        RuntimeError: If Docker is not running or not accessible.
    """
    try:
        import docker
    except ImportError as e:
        raise ImportError(
            "Docker package not installed. Install it with: pip install docker"
        ) from e

    try:
        client = docker.from_env()
        # Test connection
        client.ping()
        return client
    except Exception as e:
        raise RuntimeError(
            f"Failed to connect to Docker. Ensure Docker is running: {e}"
        ) from e


def _get_config_from_env() -> dict[str, Any]:
    """Build Docker sandbox configuration from environment variables.

    Returns:
        Dictionary with Docker configuration options.
    """
    config = {
        "image": os.getenv("DOCKER_SANDBOX_IMAGE", DEFAULT_IMAGE),
        "work_dir": os.getenv("DOCKER_SANDBOX_WORK_DIR", DEFAULT_WORK_DIR),
        "memory": int(os.getenv("DOCKER_SANDBOX_MEMORY", str(DEFAULT_MEMORY))),
        "cpu_period": int(os.getenv("DOCKER_SANDBOX_CPU_PERIOD", str(DEFAULT_CPU_PERIOD))),
        "cpu_quota": int(os.getenv("DOCKER_SANDBOX_CPU_QUOTA", str(DEFAULT_CPU_QUOTA))),
        "timeout": int(os.getenv("DOCKER_SANDBOX_TIMEOUT", str(DEFAULT_TIMEOUT))),
        "network": os.getenv("DOCKER_SANDBOX_NETWORK", DEFAULT_NETWORK),
        "container_prefix": os.getenv("DOCKER_SANDBOX_CONTAINER_PREFIX", DEFAULT_CONTAINER_PREFIX),
    }

    # Parse optional volume mounts
    volumes_env = os.getenv("DOCKER_SANDBOX_VOLUMES")
    if volumes_env:
        try:
            config["volumes"] = json.loads(volumes_env)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse DOCKER_SANDBOX_VOLUMES: {e}")

    # Parse optional security options
    security_env = os.getenv("DOCKER_SANDBOX_SECURITY_OPTS")
    if security_env:
        try:
            config["security_opt"] = json.loads(security_env)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse DOCKER_SANDBOX_SECURITY_OPTS: {e}")

    return config


class DockerSandboxBackend(BaseSandbox):
    """Docker-based sandbox backend implementing SandboxBackendProtocol.

    This implementation uses Docker containers to provide isolated execution
    environments. Each sandbox is backed by a Docker container that persists
    across multiple execute() calls until explicitly cleaned up.

    The sandbox supports:
    - Custom Docker images with pre-installed dependencies
    - Resource limits (CPU, memory)
    - Network isolation
    - Volume mounts for persistent storage
    - Security options for enhanced isolation

    Attributes:
        _container: The underlying Docker container.
        _config: Configuration dictionary with container settings.
        _default_timeout: Default timeout for command execution.
    """

    def __init__(
        self,
        container: Any,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Docker sandbox backend.

        Args:
            container: Docker container instance.
            config: Configuration dictionary with container settings.
        """
        self._container = container
        self._config = config or {}
        self._default_timeout = self._config.get("timeout", DEFAULT_TIMEOUT)
        self._work_dir = self._config.get("work_dir", DEFAULT_WORK_DIR)

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend.

        Returns:
            The Docker container ID (short form).
        """
        return self._container.short_id

    @property
    def container_name(self) -> str:
        """Get the full container name.

        Returns:
            The Docker container name.
        """
        return self._container.name

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a command in the Docker container.

        Args:
            command: Full shell command string to execute.
            timeout: Maximum time in seconds to wait for the command to complete.
                If None, uses the default timeout.

        Returns:
            ExecuteResponse with combined output, exit code, and truncation flag.

        Note:
            Commands are executed via 'sh -c' to support shell features like
            pipes, redirects, and environment variable expansion.
        """
        effective_timeout = timeout if timeout is not None else self._default_timeout

        try:
            # Execute command in container
            exit_code, output = self._container.exec_run(
                cmd=["sh", "-c", command],
                workdir=self._work_dir,
                timeout=effective_timeout,
                demux=False,  # Combine stdout and stderr
            )

            # Handle output encoding
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")

            return ExecuteResponse(
                output=output or "",
                exit_code=exit_code,
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
        """Write content to a file in the container.

        This override uses Docker's put_archive method to avoid ARG_MAX limitations
        when writing large files.

        Args:
            file_path: Absolute path in the container where the file should be written.
            content: String content to write to the file.

        Returns:
            WriteResult with the path and any error information.
        """
        import io
        import tarfile

        try:
            # Create a tar archive in memory with the file content
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                # Create tarinfo for the file
                tarinfo = tarfile.TarInfo(name=os.path.basename(file_path))
                content_bytes = content.encode("utf-8")
                tarinfo.size = len(content_bytes)

                # Add the file to the archive
                tar.addfile(tarinfo, io.BytesIO(content_bytes))

            tar_stream.seek(0)

            # Determine the directory to put the file in
            dir_path = os.path.dirname(file_path) or self._work_dir

            # Put the archive into the container
            success = self._container.put_archive(dir_path, tar_stream)

            if success:
                return WriteResult(path=file_path, files_update=None)
            else:
                return WriteResult(error=f"Failed to write file '{file_path}' to container")

        except Exception as e:
            return WriteResult(error=f"Failed to write file '{file_path}': {e}")

    def read(self, file_path: str) -> str:
        """Read a file from the container.

        Args:
            file_path: Absolute path to the file in the container.

        Returns:
            The file contents as a string.

        Raises:
            RuntimeError: If the file cannot be read.
        """
        import io
        import tarfile

        try:
            # Get the file as a tar archive from the container
            stream, stat = self._container.get_archive(file_path)

            if stat.get("size", 0) == 0:
                raise RuntimeError(f"File '{file_path}' is empty or does not exist")

            # Read the tar stream
            tar_data = io.BytesIO()
            for chunk in stream:
                tar_data.write(chunk)
            tar_data.seek(0)

            # Extract the file content
            with tarfile.open(fileobj=tar_data, mode="r") as tar:
                member = tar.getmembers()[0]
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"Failed to extract file '{file_path}' from archive")
                content = extracted.read().decode("utf-8")

            return content

        except Exception as e:
            raise RuntimeError(f"Failed to read file '{file_path}': {e}") from e

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the Docker container.

        Args:
            paths: List of absolute paths to files in the container.

        Returns:
            List of FileDownloadResponse objects with file contents or errors.
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                content = self.read(path)
                responses.append(FileDownloadResponse(path=path, content=content, error=None))
            except Exception as e:
                responses.append(FileDownloadResponse(path=path, content=None, error=str(e)))
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the Docker container.

        Args:
            files: List of (path, content) tuples.

        Returns:
            List of FileUploadResponse objects with success/error status.
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                if isinstance(content, bytes):
                    content = content.decode("utf-8")
                result = self.write(path, content)
                if result.error:
                    responses.append(FileUploadResponse(path=path, error=result.error))
                else:
                    responses.append(FileUploadResponse(path=path, error=None))
            except Exception as e:
                responses.append(FileUploadResponse(path=path, error=str(e)))
        return responses

    def cleanup(self) -> None:
        """Stop and remove the Docker container.

        This should be called when the sandbox is no longer needed to free
        resources. The container is forcefully removed if it's still running.
        """
        try:
            self._container.remove(force=True)
            logger.info(f"Removed Docker container: {self._container.short_id}")
        except Exception as e:
            logger.warning(f"Failed to remove container {self._container.short_id}: {e}")


class DockerSandboxProvider:
    """Provider for creating and managing Docker sandbox containers.

    This class handles the lifecycle of Docker containers used as sandboxes.
    It supports:
    - Creating new containers with configurable images and resources
    - Reconnecting to existing containers
    - Resource limits (CPU, memory, network)
    - Volume mounts for persistent storage
    - Security options for enhanced isolation

    Example:
        provider = DockerSandboxProvider()
        sandbox = provider.get_or_create()
        result = sandbox.execute("echo 'Hello, World!'")
        print(result.output)
        # Clean up when done
        provider.delete(sandbox_id=sandbox.id)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the Docker sandbox provider.

        Args:
            config: Optional configuration dictionary. If not provided,
                configuration is loaded from environment variables.
        """
        self._config = config or _get_config_from_env()
        self._client = _get_docker_client()

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> DockerSandboxBackend:
        """Get an existing sandbox or create a new one.

        Args:
            sandbox_id: Optional container ID or name to reconnect to.
                If None, creates a new container.
            **kwargs: Additional keyword arguments (currently unused).

        Returns:
            DockerSandboxBackend instance.

        Raises:
            RuntimeError: If the container cannot be created or found.
        """
        if kwargs:
            logger.warning(f"Received unsupported arguments: {list(kwargs.keys())}")

        if sandbox_id:
            return self._connect_existing(sandbox_id)

        return self._create_new()

    def _connect_existing(self, sandbox_id: str) -> DockerSandboxBackend:
        """Connect to an existing Docker container.

        Args:
            sandbox_id: Container ID or name.

        Returns:
            DockerSandboxBackend wrapping the existing container.

        Raises:
            RuntimeError: If the container cannot be found or is not running.
        """
        try:
            container = self._client.containers.get(sandbox_id)

            # Verify the container is running
            if container.status != "running":
                logger.info(f"Starting stopped container: {sandbox_id}")
                container.start()
                # Wait a moment for the container to be ready
                container.reload()
                if container.status != "running":
                    raise RuntimeError(f"Container {sandbox_id} failed to start")

            logger.info(f"Connected to existing Docker container: {sandbox_id}")
            return DockerSandboxBackend(container, self._config)

        except Exception as e:
            raise RuntimeError(f"Failed to connect to container '{sandbox_id}': {e}") from e

    def _create_new(self) -> DockerSandboxBackend:
        """Create a new Docker container.

        Returns:
            DockerSandboxBackend wrapping the new container.

        Raises:
            RuntimeError: If the container cannot be created.
        """
        import docker.errors

        image = self._config["image"]
        work_dir = self._config["work_dir"]
        memory = self._config["memory"]
        cpu_period = self._config["cpu_period"]
        cpu_quota = self._config["cpu_quota"]
        network = self._config["network"]
        container_prefix = self._config["container_prefix"]

        # Generate a unique container name
        container_name = f"{container_prefix}-{int(time.time() * 1000)}"

        # Build container configuration
        host_config: dict[str, Any] = {
            "mem_limit": memory,
            "cpu_period": cpu_period,
            "cpu_quota": cpu_quota,
        }

        # Add network mode
        if network:
            host_config["network_mode"] = network

        # Add volume mounts if configured
        volumes = self._config.get("volumes")
        volume_config = None
        if volumes:
            volume_config = volumes

        # Add security options if configured
        security_opt = self._config.get("security_opt")

        try:
            # Pull the image if it doesn't exist locally
            try:
                self._client.images.get(image)
            except docker.errors.ImageNotFound:
                logger.info(f"Pulling Docker image: {image}")
                self._client.images.pull(image)
                logger.info(f"Successfully pulled image: {image}")

            # Create and start the container
            container = self._client.containers.create(
                image=image,
                name=container_name,
                working_dir=work_dir,
                command="tail -f /dev/null",  # Keep container running
                volumes=volume_config,
                host_config=host_config,
                security_opt=security_opt,
                detach=True,
                tty=True,
            )

            container.start()
            logger.info(f"Created and started Docker container: {container.short_id}")

            # Wait for container to be ready
            self._wait_for_ready(container)

            return DockerSandboxBackend(container, self._config)

        except Exception as e:
            raise RuntimeError(f"Failed to create Docker container: {e}") from e

    def _wait_for_ready(
        self,
        container: Any,
        timeout: int = 30,
        poll_interval: float = 0.5,
    ) -> None:
        """Wait for the container to be ready to accept commands.

        Args:
            container: Docker container instance.
            timeout: Maximum time to wait in seconds.
            poll_interval: Time between readiness checks in seconds.

        Raises:
            RuntimeError: If the container doesn't become ready within the timeout.
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            container.reload()
            if container.status == "running":
                # Try executing a simple command to verify readiness
                try:
                    exit_code, _ = container.exec_run("echo ready", timeout=5)
                    if exit_code == 0:
                        return
                except Exception:
                    pass

            time.sleep(poll_interval)

        raise RuntimeError(f"Container failed to become ready within {timeout} seconds")

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a Docker container.

        Args:
            sandbox_id: Container ID or name to delete.
            **kwargs: Additional keyword arguments (currently unused).
        """
        try:
            container = self._client.containers.get(sandbox_id)
            container.remove(force=True)
            logger.info(f"Deleted Docker container: {sandbox_id}")
        except Exception as e:
            logger.warning(f"Failed to delete container {sandbox_id}: {e}")


def create_docker_sandbox(
    sandbox_id: str | None = None,
) -> DockerSandboxBackend:
    """Create or reconnect to a Docker sandbox.

    This is the factory function registered in the SANDBOX_FACTORIES dictionary.
    It creates a new Docker container or reconnects to an existing one.

    Environment variables:
        DOCKER_SANDBOX_IMAGE: Base image (default: python:3.11-slim)
        DOCKER_SANDBOX_WORK_DIR: Working directory (default: /workspace)
        DOCKER_SANDBOX_MEMORY: Memory limit in bytes (default: 2GB)
        DOCKER_SANDBOX_CPU_PERIOD: CPU period (default: 100000)
        DOCKER_SANDBOX_CPU_QUOTA: CPU quota (default: 100000 = 1 CPU)
        DOCKER_SANDBOX_TIMEOUT: Default timeout in seconds (default: 300)
        DOCKER_SANDBOX_NETWORK: Network mode (default: bridge)
        DOCKER_SANDBOX_VOLUMES: JSON dict of volume mounts (optional)
        DOCKER_SANDBOX_SECURITY_OPTS: JSON list of security options (optional)
        DOCKER_SANDBOX_CONTAINER_PREFIX: Container name prefix (default: open-swe-sandbox)

    Args:
        sandbox_id: Optional existing container ID to reconnect to.
            If None, creates a new container.

    Returns:
        DockerSandboxBackend instance implementing SandboxBackendProtocol.

    Raises:
        ImportError: If the 'docker' package is not installed.
        RuntimeError: If Docker is not running or the container cannot be created.
    """
    provider = DockerSandboxProvider()
    backend = provider.get_or_create(sandbox_id=sandbox_id)
    _update_thread_sandbox_metadata(backend.id)
    return backend


def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Update thread metadata with sandbox_id.

    This is a best-effort function that attempts to update the LangGraph thread
    metadata with the sandbox ID for persistence across agent invocations.
    Failures are silently ignored.
    """
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
        # Best-effort: ignore failures (no config context, client unavailable, etc.)
        pass


# Convenience functions for container management

def list_docker_sandboxes(prefix: str | None = None) -> list[dict[str, Any]]:
    """List all Docker sandbox containers.

    Args:
        prefix: Optional container name prefix filter. Defaults to the configured prefix.

    Returns:
        List of dictionaries with container information (id, name, status, image).
    """
    client = _get_docker_client()
    container_prefix = prefix or os.getenv("DOCKER_SANDBOX_CONTAINER_PREFIX", DEFAULT_CONTAINER_PREFIX)

    containers = []
    for container in client.containers.list(all=True):
        if container.name.startswith(container_prefix):
            containers.append({
                "id": container.short_id,
                "name": container.name,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else container.image.id,
            })

    return containers


def cleanup_all_docker_sandboxes(prefix: str | None = None) -> int:
    """Remove all Docker sandbox containers.

    This is useful for cleanup after testing or development.

    Args:
        prefix: Optional container name prefix filter. Defaults to the configured prefix.

    Returns:
        Number of containers removed.
    """
    client = _get_docker_client()
    container_prefix = prefix or os.getenv("DOCKER_SANDBOX_CONTAINER_PREFIX", DEFAULT_CONTAINER_PREFIX)

    count = 0
    for container in client.containers.list(all=True):
        if container.name.startswith(container_prefix):
            try:
                container.remove(force=True)
                count += 1
                logger.info(f"Removed container: {container.name}")
            except Exception as e:
                logger.warning(f"Failed to remove container {container.name}: {e}")

    return count