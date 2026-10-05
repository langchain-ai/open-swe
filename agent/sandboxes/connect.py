"""Reach a thread's existing sandbox from outside its run, whichever kind it is."""

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.bridge.backend import BridgeSandboxBackend
from agent.bridge.store import Bridge
from agent.sandboxes.providers.registry import create_sandbox


async def connect_sandbox(
    sandbox_id: str, *, thread_id: str | None = None
) -> SandboxBackendProtocol:
    """The sandbox a thread's ``sandbox_id`` names.

    A ``bridge:`` id is the user's own machine, which the deployment's sandbox
    provider has never heard of; handing it one would fail as a missing box.
    """
    bridge_id = Bridge.bridge_id_of(sandbox_id)
    if bridge_id is not None:
        return await BridgeSandboxBackend.connect(thread_id, bridge_id)
    return await create_sandbox(sandbox_id)
