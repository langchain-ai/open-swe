"""Run the scripts in ``openswe/sandbox_scripts`` inside a thread's sandbox."""

import base64
import hashlib
import json
import secrets
import shlex
from collections.abc import Mapping
from functools import cache, cached_property
from importlib import resources
from typing import TYPE_CHECKING

from pydantic import JsonValue

if TYPE_CHECKING:
    # The dashboard runs sandbox scripts too, and must not load the agent stack.
    from deepagents.backends.protocol import SandboxBackendProtocol


# Linux caps one command-line argument at 128 KiB; the command wraps it, so stay well under.
_MAX_ARGUMENT_BYTES = 64 * 1024


@cache
def source(name: str) -> str:
    """The text of ``openswe/sandbox_scripts/<name>``, e.g. ``"monitor.py"``."""
    return resources.files("openswe.sandbox_scripts").joinpath(name).read_text(encoding="utf-8")


def encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class SandboxScriptError(RuntimeError):
    """The script refused a request, with a reason fit to show the agent."""


class SandboxScript:
    """A script kept in a sandbox directory under a name carrying its hash.

    Each call runs it with a base64 JSON request and reads back the one JSON
    line it prints. The copy is written only when that version is missing, so
    the common case is one exec, and a sandbox always runs the script this
    backend ships. A script made with ``reads_request_files`` may get a request
    too long for a command line: it is written to a file instead, and the script
    gets ``@<path>`` to read it from. Other scripts refuse such a request.
    """

    def __init__(
        self, name: str, root: str, *, text: str | None = None, reads_request_files: bool = False
    ) -> None:
        self.name = name
        self.root = root
        self._text = text
        self._reads_request_files = reads_request_files

    @cached_property
    def text(self) -> str:
        return self._text if self._text is not None else source(self.name)

    @cached_property
    def path(self) -> str:
        stem = self.name.removesuffix(".py")
        digest = hashlib.sha256(self.text.encode()).hexdigest()[:16]
        return f"{self.root}/{stem}-{digest}.py"

    async def _write_request(
        self, backend: SandboxBackendProtocol, argument: str, *, timeout: int
    ) -> str:
        """Write ``argument`` to a file through the shell, in pieces that each fit a command line.

        Not ``upload_files``: a local sandbox maps an uploaded path under its own root,
        where the shell running the script would not find it.
        """
        path = shlex.quote(f"/tmp/open-swe-request-{secrets.token_hex(16)}")
        for at in range(0, len(argument), _MAX_ARGUMENT_BYTES):
            piece = shlex.quote(argument[at : at + _MAX_ARGUMENT_BYTES])
            written = await backend.aexecute(f"printf %s {piece} >> {path}", timeout=timeout)
            if written.exit_code not in (0, None):
                raise RuntimeError(f"could not write a request for {self.name}")
        return path.strip("'")

    async def run(
        self,
        backend: SandboxBackendProtocol,
        request: Mapping[str, JsonValue],
        *,
        timeout: int = 30,
    ) -> dict[str, JsonValue]:
        """The script's answer to ``request``; raises ``SandboxScriptError`` when it refuses."""
        script = shlex.quote(self.path)
        # Fresh each call, so nothing a script or the sandbox prints can pass for it.
        missing = f"open-swe-script-missing-{secrets.token_hex(16)}"
        argument = encoded(json.dumps(request))
        if len(argument) > _MAX_ARGUMENT_BYTES:
            if not self._reads_request_files:
                raise RuntimeError(f"the request is too long for {self.name}'s command line")
            argument = f"@{await self._write_request(backend, argument, timeout=timeout)}"
        run = (
            "command -v python3 >/dev/null || "
            '{ echo \'{"error": "this needs python3 in the sandbox"}\'; exit 0; }; '
            f"python3 {script} {shlex.quote(argument)}"
        )
        response = await backend.aexecute(
            f"[ -f {script} ] || {{ echo {missing}; exit 0; }}; {run}", timeout=timeout
        )
        if response.output.strip() == missing:
            response = await backend.aexecute(
                f"mkdir -p {shlex.quote(self.root)} && "
                f"printf %s {shlex.quote(encoded(self.text))} | base64 -d > {script}.$$ && "
                f"mv {script}.$$ {script}; {run}",
                timeout=timeout,
            )
        lines = response.output.strip().splitlines()
        try:
            result = json.loads(lines[-1]) if lines else None
        except json.JSONDecodeError:
            result = None
        if not isinstance(result, dict):
            raise RuntimeError(
                response.output.strip() or f"{self.name} failed with exit code {response.exit_code}"
            )
        error = result.get("error")
        if isinstance(error, str):
            raise SandboxScriptError(error)
        return result
