import asyncio
import inspect
import os
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

SandboxFactory = Callable[..., Any]


class SandboxGoneError(RuntimeError):
    """The sandbox a thread is bound to no longer exists.

    Distinct from a sandbox that is merely unreachable: a deleted one holds no
    working tree, so callers recreate instead of failing the run.
    """


SANDBOX_FACTORIES: dict[str, tuple[str, str]] = {
    "langsmith": ("agent.sandboxes.providers.langsmith", "create_langsmith_sandbox"),
    "daytona": ("agent.sandboxes.providers.daytona", "create_daytona_sandbox"),
    "modal": ("agent.sandboxes.providers.modal", "create_modal_sandbox"),
    "runloop": ("agent.sandboxes.providers.runloop", "create_runloop_sandbox"),
    "e2b": ("agent.sandboxes.providers.e2b", "create_e2b_sandbox"),
    "local": ("agent.sandboxes.providers.local", "create_local_sandbox"),
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
    mem_bytes: int | None = None,
    vcpus: int | None = None,
    fs_capacity_bytes: int | None = None,
    create_params: dict[str, Any] | None = None,
) -> "SandboxBackendProtocol":
    """Create or reconnect to a sandbox using the configured provider.

    The provider is selected via the SANDBOX_TYPE environment variable.
    Supported values: langsmith (default), daytona, modal, runloop, e2b, local.

    langsmith and modal provision natively async. local stays on
    ``asyncio.to_thread`` because ``LocalShellBackend`` setup performs synchronous
    filesystem I/O. daytona, e2b and runloop stay there because their
    ``langchain_*`` wrappers bind synchronous SDK handles.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect to.
        snapshot_id: Optional snapshot to boot a new sandbox from. Only the
            langsmith provider honors this; others ignore it. When omitted the
            langsmith provider uses DEFAULT_SANDBOX_SNAPSHOT_ID or its root snapshot.
        mem_bytes: Optional memory capacity override for a new LangSmith sandbox.
        vcpus: Optional virtual CPU count override for a new LangSmith sandbox.
        fs_capacity_bytes: Optional filesystem capacity override for a new LangSmith sandbox.
        create_params: Optional additional LangSmith sandbox create-body fields.

    Returns:
        A sandbox backend implementing SandboxBackendProtocol.
    """
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    factory = _load_sandbox_factory(sandbox_type)
    if sandbox_type == "langsmith":
        options = {
            key: value
            for key, value in {
                "snapshot_id": snapshot_id,
                "mem_bytes": mem_bytes,
                "vcpus": vcpus,
                "fs_capacity_bytes": fs_capacity_bytes,
                "create_params": create_params,
            }.items()
            if value is not None
        }
        return await factory(sandbox_id, **options)
    if inspect.iscoroutinefunction(factory):
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
        from agent.sandboxes.providers.langsmith import LangSmithProvider

        LangSmithProvider.validate_startup_config()
