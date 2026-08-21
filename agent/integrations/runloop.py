"""Runloop devbox provider."""

import asyncio
from typing import Any, cast

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_runloop import RunloopSandbox
from runloop_api_client import Client, NotFoundError

from ..config import runloop_api_key
from ..sandboxes.providers import SandboxGoneError, SandboxProvider


class RunloopProvider(SandboxProvider):
    """Runloop devboxes.

    Cannot boot from an Open SWE snapshot: a devbox starts from a Runloop
    blueprint, which is built and named in Runloop's own registry.

    Says nothing about the work dir — a blueprint chooses it, so the devbox's
    own shell is the only thing that knows it.
    """

    async def connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        return await asyncio.to_thread(self._connect, sandbox_id)

    async def create(self, *, snapshot_id: str | None = None) -> SandboxBackendProtocol:
        if snapshot_id is not None:
            msg = (
                f"Runloop cannot boot from snapshot {snapshot_id!r}; a devbox starts from a "
                "Runloop blueprint"
            )
            raise ValueError(msg)
        return await asyncio.to_thread(self._create)

    def _connect(self, sandbox_id: str) -> SandboxBackendProtocol:
        try:
            devbox = self._client().devboxes.retrieve(sandbox_id)
        except NotFoundError as e:
            msg = f"Failed to connect to existing devbox '{sandbox_id}': {e}"
            raise SandboxGoneError(msg) from e
        return RunloopSandbox(devbox=cast(Any, devbox))

    def _create(self) -> SandboxBackendProtocol:
        return RunloopSandbox(devbox=cast(Any, self._client().devboxes.create()))

    @staticmethod
    def _client() -> Client:
        api_key = runloop_api_key()
        if not api_key:
            raise ValueError("RUNLOOP_API_KEY environment variable is required")
        return Client(bearer_token=api_key)
