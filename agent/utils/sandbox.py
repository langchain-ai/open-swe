import asyncio
import os
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

SandboxFactory = Callable[..., Any]

SANDBOX_FACTORIES: dict[str, tuple[str, str]] = {
    "langsmith": ("agent.integrations.langsmith", "create_langsmith_sandbox"),
    "daytona": ("agent.integrations.daytona", "create_daytona_sandbox"),
    "modal": ("agent.integrations.modal", "create_modal_sandbox"),
    "runloop": ("agent.integrations.runloop", "create_runloop_sandbox"),
    "e2b": ("agent.integrations.e2b", "create_e2b_sandbox"),
    "local": ("agent.integrations.local", "create_local_sandbox"),
}


def _load_sandbox_factory(sandbox_type: str) -> SandboxFactory:
    factory_path = SANDBOX_FACTORIES.get(sandbox_type)
    if factory_path is None:
        supported = ", ".join(sorted(SANDBOX_FACTORIES))
        raise ValueError(f"Invalid sandbox type: {sandbox_type}. Supported types: {supported}")
    module_name, function_name = factory_path
    factory = getattr(import_module(module_name), function_name)
    if not callable(factory):
        raise TypeError(f"Sandbox factory {module_name}.{function_name} is not callable")
    return factory


async def create_sandbox(
    sandbox_id: str | None = None,
    *,
    snapshot_id: str | None = None,
) -> "SandboxBackendProtocol":
    """Create or reconnect to a sandbox using the configured provider.

    The provider is selected via the SANDBOX_TYPE environment variable.
    Supported values: langsmith (default), daytona, modal, runloop, e2b, local.

    The langsmith provider provisions natively async; the other providers wrap
    sync SDKs and are bridged onto the event loop with ``asyncio.to_thread``.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
        snapshot_id: Optional snapshot to boot a new sandbox from. Only the
            langsmith provider honors this; others ignore it. When omitted the
            langsmith provider falls back to DEFAULT_SANDBOX_SNAPSHOT_ID.

    Returns:
        A sandbox backend implementing SandboxBackendProtocol.
    """
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    factory = _load_sandbox_factory(sandbox_type)
    if sandbox_type == "langsmith":
        if snapshot_id is not None:
            return await factory(sandbox_id, snapshot_id=snapshot_id)
        return await factory(sandbox_id)
    return await asyncio.to_thread(factory, sandbox_id)


def validate_sandbox_startup_config() -> None:
    """Validate the configured sandbox provider's env vars at server startup.

    Raises ValueError if the active provider's configuration is invalid.
    Called from the FastAPI lifespan hook so errors surface at boot rather
    than on the first sandbox creation.
    """
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type == "langsmith":
        from agent.integrations.langsmith import LangSmithProvider

        LangSmithProvider.validate_startup_config()
