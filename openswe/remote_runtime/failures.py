"""What an operation that raised answers, so the model can see the failure and act on it.

A sandbox that stopped answering is the one failure nothing the model does next
can get past: the person is told, and the MDA run ends.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from langsmith.sandbox import (
    ResourceNotFoundError,
    SandboxConnectionError,
    SandboxServerReloadError,
)

from openswe.middleware.sandbox_circuit_breaker import (
    extract_sandbox_id,
    post_sandbox_unreachable_notification,
)
from openswe.sandboxes.retry import is_transient_sandbox_error

logger = logging.getLogger(__name__)

SANDBOX_TRANSIENT = "sandbox_transient"


@dataclass(frozen=True)
class OperationFailure:
    """The error result an operation answers with in place of what it raised."""

    content: str
    sandbox_unreachable: bool = False

    @classmethod
    async def of(
        cls, exc: Exception, *, operation: str, config: Mapping[str, object]
    ) -> OperationFailure:
        if is_transient_sandbox_error(exc):
            # The command never started, so nothing is known to be wrong with the
            # sandbox: ending the run here would turn a gateway blip into an abandoned one.
            logger.warning("Transient sandbox error", extra={"operation": operation})
            payload: dict[str, str] = {
                "status": "error",
                "error_type": type(exc).__name__,
                "previous_error": str(exc),
                "recovery": SANDBOX_TRANSIENT,
                "error": (
                    "The sandbox connection was rejected before this command started, so "
                    "nothing ran and nothing changed."
                ),
                "name": operation,
            }
            if sandbox_id := extract_sandbox_id(str(exc)):
                payload["sandbox_id"] = sandbox_id
            return cls(json.dumps(payload))
        payload = {
            "error": str(exc),
            "error_type": type(exc).__name__,
            "status": "error",
            "name": operation,
        }
        if not _sandbox_unreachable(exc):
            logger.warning("Operation failed", extra={"operation": operation}, exc_info=True)
            return cls(json.dumps(payload))
        logger.error("The sandbox did not answer", extra={"operation": operation}, exc_info=True)
        try:
            await post_sandbox_unreachable_notification(
                config, sandbox_id=extract_sandbox_id(str(exc))
            )
        except Exception:
            logger.exception("Could not tell the person their sandbox is gone")
        return cls(json.dumps(payload), sandbox_unreachable=True)


def _sandbox_unreachable(exc: Exception) -> bool:
    """Whether the failure means the sandbox itself did not answer.

    A connection error does, once the two that carry their own meaning are
    excluded: a retryable rejection never started the command, and a server
    reload left it running. ``ResourceNotFoundError`` qualifies only for the
    sandbox itself; a missing file is the operation's own failure.
    """
    if isinstance(exc, SandboxConnectionError):
        return not isinstance(exc, SandboxServerReloadError)
    return isinstance(exc, ResourceNotFoundError) and exc.resource_type == "sandbox"
