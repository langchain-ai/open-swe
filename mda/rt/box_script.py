"""Run the scripts in ``rt/sandbox_scripts`` in the thread's box.

Each is kept in the box under a name carrying its hash, and written only when that
version is missing, so the common case is one command, and a box always runs the
version this runtime ships. A script gets its request as base64 JSON and answers
with one JSON line; ``{"error": ...}`` is its refusal.
"""

import base64
import hashlib
import json
import secrets
import shlex
from functools import cached_property
from pathlib import Path
from typing import Final

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import JsonValue, TypeAdapter, ValidationError

_SCRIPTS: Final = Path(__file__).with_name("sandbox_scripts")
_answer = TypeAdapter(dict[str, JsonValue])


class BoxScriptError(RuntimeError):
    """The script refused the request, or answered with no JSON line."""


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class BoxScript:
    """``rt/sandbox_scripts/<name>``, kept in the box under ``root``."""

    def __init__(self, name: str, root: str) -> None:
        self.name = name
        self.root = root

    @cached_property
    def text(self) -> str:
        return _SCRIPTS.joinpath(self.name).read_text(encoding="utf-8")

    @cached_property
    def path(self) -> str:
        digest = hashlib.sha256(self.text.encode()).hexdigest()[:16]
        return f"{self.root}/{self.name.removesuffix('.py')}-{digest}.py"

    async def run(
        self, box: SandboxBackendProtocol, request: dict[str, JsonValue], *, timeout: int = 30
    ) -> dict[str, JsonValue]:
        script = shlex.quote(self.path)
        # Fresh each call, so nothing the box prints can pass for it.
        missing = f"open-swe-script-missing-{secrets.token_hex(16)}"
        run = (
            "command -v python3 >/dev/null || "
            '{ echo \'{"error": "this needs python3 in the sandbox"}\'; exit 0; }; '
            f"python3 {script} {shlex.quote(_encoded(json.dumps(request)))}"
        )
        response = await box.aexecute(
            f"[ -f {script} ] || {{ echo {missing}; exit 0; }}; {run}", timeout=timeout
        )
        if response.output.strip() == missing:
            response = await box.aexecute(
                f"mkdir -p {shlex.quote(self.root)} && "
                f"printf %s {shlex.quote(_encoded(self.text))} | base64 -d > {script}.$$ && "
                f"mv {script}.$$ {script}; {run}",
                timeout=timeout,
            )
        lines = response.output.strip().splitlines()
        try:
            answer = _answer.validate_json(lines[-1]) if lines else None
        except ValidationError:
            answer = None
        if answer is None:
            raise BoxScriptError(
                response.output.strip() or f"{self.name} failed with exit code {response.exit_code}"
            )
        if isinstance(error := answer.get("error"), str):
            raise BoxScriptError(error)
        return answer
