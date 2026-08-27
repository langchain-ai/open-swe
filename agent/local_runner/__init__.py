"""Running an agent's commands on the user's own machine, from the cloud server."""

from typing import TYPE_CHECKING, Any

from .broker import LocalDeviceUnreachableError, LocalRunnerBroker, runner_broker

if TYPE_CHECKING:
    from .backend import LocalMachineBackend

__all__ = [
    "LocalDeviceUnreachableError",
    "LocalMachineBackend",
    "LocalRunnerBroker",
    "runner_broker",
]


def __getattr__(name: str) -> Any:
    """Defer the backend import: it pulls in deepagents, and the FastAPI app
    reaches ``routes`` through this package without needing the agent stack."""
    if name == "LocalMachineBackend":
        from .backend import LocalMachineBackend

        return LocalMachineBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
