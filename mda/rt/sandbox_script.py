"""Run the scripts in ``rt/sandbox_scripts`` in the thread's sandbox.

Each call is one command that pipes the script this runtime ships into the sandbox's
``python3``, so a sandbox always runs that version and nothing is kept in it. A sandbox's
first command is slow, so a second one to install the script would cost that
twice. A script gets its request as base64 JSON and answers with one JSON line;
``{"error": ...}`` is its refusal.
"""

import base64
import json
import shlex
from functools import cached_property
from pathlib import Path
from typing import Final

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import JsonValue, TypeAdapter, ValidationError

_SCRIPTS: Final = Path(__file__).with_name("sandbox_scripts")
_answer = TypeAdapter(dict[str, JsonValue])


class SandboxScriptError(RuntimeError):
    """The script refused the request, or answered with no JSON line."""


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class SandboxScript:
    """``rt/sandbox_scripts/<name>``, run in the thread's sandbox."""

    def __init__(self, name: str) -> None:
        self.name = name

    @cached_property
    def _source(self) -> str:
        return shlex.quote(_encoded(_SCRIPTS.joinpath(self.name).read_text(encoding="utf-8")))

    async def run(
        self, sandbox: SandboxBackendProtocol, request: dict[str, JsonValue], *, timeout: int = 30
    ) -> dict[str, JsonValue]:
        response = await sandbox.aexecute(
            "command -v python3 >/dev/null || "
            '{ echo \'{"error": "this needs python3 in the sandbox"}\'; exit 0; }; '
            f"printf %s {self._source} | base64 -d | "
            f"python3 - {shlex.quote(_encoded(json.dumps(request)))}",
            timeout=timeout,
        )
        lines = response.output.strip().splitlines()
        try:
            answer = _answer.validate_json(lines[-1]) if lines else None
        except ValidationError:
            answer = None
        if answer is None:
            raise SandboxScriptError(
                response.output.strip() or f"{self.name} failed with exit code {response.exit_code}"
            )
        if isinstance(error := answer.get("error"), str):
            raise SandboxScriptError(error)
        return answer
