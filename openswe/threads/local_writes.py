"""Seal this deployment's Agent Server against new threads and runs.

Every thread is made and run on Managed Deep Agents; the Agent Server here only
serves reads of the threads made before that. Its routes, its in-process client,
its cron scheduler and its v2 command protocol all create threads and runs through
the same few operations, so ``LOCAL_WRITES.seal()`` replaces each of them, on every
operations class the server may use, with one that always raises.
"""

import importlib
import logging
from typing import Final, NoReturn

from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)

# Every module that defines the Agent Server's operations: the runtime edition's own
# (``langgraph_runtime.ops`` resolves to it) and the gRPC core API's.
_OPS_MODULES: Final = ("langgraph_runtime.ops", "langgraph_api.grpc.ops")
# The operations that make a thread, a run, or a cron that makes runs.
_CREATES: Final = (("Threads", "put"), ("Threads", "copy"), ("Runs", "put"), ("Crons", "put"))
_REFUSED: Final = (
    "This deployment's Agent Server takes no new threads or runs: "
    "every thread is created and run on Managed Deep Agents."
)


class LocalAgentServerWriteError(HTTPException):
    """A thread, run or cron was about to be created on this deployment's Agent Server."""

    def __init__(self, operation: str) -> None:
        super().__init__(status_code=410, detail=_REFUSED)
        self.operation = operation


class _Refusal:
    """Stands in for one create operation and raises whenever it is called."""

    def __init__(self, operation: str) -> None:
        self.operation = operation

    async def __call__(self, *args: object, **kwargs: object) -> NoReturn:
        logger.error(
            "Refused to create on the local Agent Server", extra={"operation": self.operation}
        )
        raise LocalAgentServerWriteError(self.operation)


class LocalWrites:
    """Whether this process's Agent Server operations still create threads and runs."""

    def __init__(self) -> None:
        self.sealed: list[str] = []

    def seal(self) -> None:
        """Replace every create operation with a refusal; raise if one can't be found."""
        found: set[tuple[str, str]] = set()
        operations: list[tuple[type, str, str]] = []
        for module_name in _OPS_MODULES:
            module = importlib.import_module(module_name)
            for class_name, method in _CREATES:
                ops = getattr(module, class_name, None)
                if ops is None:
                    continue
                operation = f"{module_name}.{class_name}.{method}"
                if not callable(getattr(ops, method, None)):
                    raise RuntimeError(f"Agent Server operation {operation} not found to seal")
                found.add((class_name, method))
                operations.append((ops, method, operation))
        missing = set(_CREATES) - found
        if missing:
            raise RuntimeError(f"Agent Server operations not found to seal: {sorted(missing)}")
        for ops, method, operation in operations:
            setattr(ops, method, staticmethod(_Refusal(operation)))
            self.sealed.append(operation)
        logger.info("Sealed the local Agent Server", extra={"operations": self.sealed})


LOCAL_WRITES: Final = LocalWrites()
