"""Tools that prefer-tools-in-sandbox threads reach through the sandbox instead of directly."""

from collections.abc import Awaitable, Callable
from types import FunctionType

SANDBOX_ONLY_TOOLS: set[str] = set()
CURL_REPLACED_TOOLS: set[str] = set()


def _register[F: Callable[..., Awaitable[object]]](registry: set[str], tool: F) -> F:
    if not isinstance(tool, FunctionType):
        raise TypeError("Sandbox preference requires a function")
    registry.add(tool.__name__)
    return tool


def sandbox_only[F: Callable[..., Awaitable[object]]](tool: F) -> F:
    """Hide a large-result tool from the model; the sandbox tools endpoint still serves it."""
    return _register(SANDBOX_ONLY_TOOLS, tool)


def replaced_by_curl[F: Callable[..., Awaitable[object]]](tool: F) -> F:
    """Drop a tool entirely, since curl in the sandbox does the same job."""
    return _register(CURL_REPLACED_TOOLS, tool)
