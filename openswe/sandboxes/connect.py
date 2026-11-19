"""Reach a thread's existing sandbox from outside its run."""

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.sandboxes.providers.registry import connect_existing_sandbox


async def connect_sandbox(sandbox_id: str) -> SandboxBackendProtocol:
    """The sandbox a thread's ``sandbox_id`` names."""
    return await connect_existing_sandbox(sandbox_id)
