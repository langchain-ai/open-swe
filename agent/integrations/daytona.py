"""Daytona sandbox provider."""

import asyncio
from collections.abc import Mapping
from typing import Any

from daytona import (
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaConfig,
    DaytonaNotFoundError,
)
from daytona import Sandbox as DaytonaSdkSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_daytona import DaytonaSandbox

from ..config import daytona_api_key, daytona_snapshot
from ..sandboxes.providers import SandboxGoneError, SandboxProvider, SandboxResources

DEFAULT_DAYTONA_SANDBOX_SNAPSHOT = "daytonaio/sandbox:0.6.0"


def _get_daytona_sandbox_params() -> CreateSandboxFromSnapshotParams:
    return CreateSandboxFromSnapshotParams(
        snapshot=daytona_snapshot(DEFAULT_DAYTONA_SANDBOX_SNAPSHOT)
    )


class DaytonaBackend(DaytonaSandbox):
    """A ``DaytonaSandbox`` that keeps its SDK handle reachable.

    The wrapper hides the Daytona sandbox it was built from, and the SDK is the
    only thing that knows where the box puts its work dir.
    """

    def __init__(self, sandbox: DaytonaSdkSandbox) -> None:
        super().__init__(sandbox=sandbox)
        self.daytona_sandbox = sandbox


class DaytonaProvider(SandboxProvider):
    """Daytona sandboxes.

    Cannot boot from an Open SWE snapshot: Daytona's snapshots are container
    images in its own registry, selected once per deployment through
    ``DAYTONA_SANDBOX_SNAPSHOT``, and none of them can be an id minted by the
    environment or repo-snapshot builds.
    """

    async def connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        return await asyncio.to_thread(self._connect, sandbox_id)

    async def create(
        self,
        *,
        snapshot_id: str | None = None,
        resources: SandboxResources | None = None,
        create_params: Mapping[str, Any] | None = None,
    ) -> SandboxBackendProtocol:
        if snapshot_id is not None:
            msg = (
                f"Daytona cannot boot from snapshot {snapshot_id!r}; its image is set by "
                "DAYTONA_SANDBOX_SNAPSHOT"
            )
            raise ValueError(msg)
        self._reject_sizing("Daytona", resources, create_params)
        return await asyncio.to_thread(self._create)

    async def work_dir(self, backend: SandboxBackendProtocol) -> str | None:
        if not isinstance(backend, DaytonaBackend):
            return None
        return await asyncio.to_thread(backend.daytona_sandbox.get_work_dir)

    def _connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        try:
            sandbox = self._client().get(sandbox_id)
        except DaytonaNotFoundError as e:
            msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
            raise SandboxGoneError(msg) from e
        return DaytonaBackend(sandbox)

    def _create(self) -> SandboxBackendProtocol:
        return DaytonaBackend(self._client().create(params=_get_daytona_sandbox_params()))

    def _client(self) -> Daytona:
        api_key = daytona_api_key()
        if not api_key:
            raise ValueError("DAYTONA_API_KEY environment variable is required")
        return Daytona(config=DaytonaConfig(api_key=api_key))
