"""One in-memory ``SandboxBackendProtocol`` with scripted command results.

Resolution order for a command, first hit wins: ``respond`` (a callable, for
the tests that dispatch on the command's shape), ``results`` (exact-command
lookup), ``script`` (consumed in order), then ``default``. A result may be an
``ExecuteResponse``, a plain string (exit code 0), or an exception to raise.
With no configuration at all the backend echoes what it was asked to run.

It deliberately does **not** subclass ``BaseSandbox``: the proxy's
capture-offload fallback keys off the absence of ``aexecute_with_offload``, and
tests need a backend that lacks it.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from deepagents.backends.protocol import DeleteResult, ExecuteResponse, SandboxBackendProtocol

Result = ExecuteResponse | str | BaseException


class FakeSandboxBackend(SandboxBackendProtocol):
    def __init__(
        self,
        sandbox_id: str = "sandbox-1",
        *,
        respond: Callable[[str], Result | None] | None = None,
        results: Mapping[str, Result] | None = None,
        script: Sequence[Result] | None = None,
        default: Result | None = None,
        provider: Any = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._respond = respond
        self._results = dict(results or {})
        self._script = list(script or [])
        self._default = default
        self.sandbox = provider
        self.commands: list[str] = []
        self.timeouts: list[int | None] = []
        self.deleted: list[str] = []

    @property
    def id(self) -> str:
        return self._sandbox_id

    def _result_for(self, command: str) -> Result | None:
        if self._respond is not None:
            result = self._respond(command)
            if result is not None:
                return result
        if command in self._results:
            return self._results[command]
        if self._script:
            return self._script.pop(0)
        return self._default

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.commands.append(command)
        self.timeouts.append(timeout)
        result = self._result_for(command)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            return ExecuteResponse(output=f"{self.id}: {command}: {timeout}", exit_code=0)
        if isinstance(result, str):
            return ExecuteResponse(output=result, exit_code=0, truncated=False)
        return result

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self.execute(command, timeout=timeout)

    async def adelete(self, file_path: str) -> DeleteResult:
        self.deleted.append(file_path)
        return DeleteResult(path=file_path)
