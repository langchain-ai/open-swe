"""The directory a thread's agent works in."""

import logging
import posixpath
import re
import shlex
from collections.abc import Mapping
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from openswe.utils.thread_ops import langgraph_client

if TYPE_CHECKING:
    # Dashboard routes read the directory from metadata without paying for deepagents.
    from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

# The key in both the thread's graph state and its metadata.
CONTEXT_DIRECTORY_KEY = "context_directory"
_GITHUB_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
# Prints the directory, then its checkout's root and origin, one per line ("" when absent).
_INSPECT = """cd -- {path} || exit 1
pwd -P
git rev-parse --show-toplevel 2>/dev/null || echo
git remote get-url origin 2>/dev/null || echo"""


class ContextDirectory(BaseModel):
    """Where a thread's agent works: its commands run here and relative paths resolve here.

    Kept in the thread's graph state for the agent's own tools, and in its metadata
    for everything else that looks into the sandbox.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    # ``owner/name`` of the GitHub repository whose checkout holds ``path``.
    repo: str | None = None
    # ``path`` within that checkout, "" at its root; None outside any git checkout.
    subpath: str | None = None

    @classmethod
    def of(cls, values: object) -> Self | None:
        """The directory recorded in a thread's state or metadata, if one is open."""
        raw = values.get(CONTEXT_DIRECTORY_KEY) if isinstance(values, Mapping) else None
        if not isinstance(raw, Mapping):
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            logger.warning("Ignoring a malformed context directory", exc_info=True)
            return None

    @classmethod
    async def open(cls, sandbox: SandboxBackendProtocol, path: str) -> Self:
        """Inspect ``path`` in ``sandbox``, which must be an existing directory."""
        result = await sandbox.aexecute(_INSPECT.format(path=shlex.quote(path)))
        lines = [line.strip() for line in result.output.splitlines()]
        if result.exit_code != 0 or len(lines) < 3 or not lines[-3].startswith("/"):
            raise ValueError(f"{path} is not a directory in the sandbox: {result.output.strip()}")
        directory, checkout, origin = lines[-3:]
        remote = _GITHUB_REMOTE.search(origin)
        return cls(
            path=directory,
            repo=f"{remote.group(1)}/{remote.group(2)}" if remote and checkout else None,
            subpath=_within(directory, checkout) if checkout else None,
        )

    @property
    def checkout(self) -> str | None:
        """The root of the git checkout holding this directory."""
        if self.subpath is None:
            return None
        return self.path.removesuffix(f"/{self.subpath}") if self.subpath else self.path

    def resolve(self, path: str) -> str:
        """``path`` as an absolute sandbox path, relative ones taken from here."""
        return posixpath.normpath(posixpath.join(self.path, path))

    def command(self, command: str) -> str:
        """``command`` as run from here.

        The group keeps ``||`` and ``;`` in ``command`` from running elsewhere when
        the directory is gone; its newlines keep a trailing comment or heredoc whole.
        """
        return f"cd {shlex.quote(self.path)} && {{\n{command}\n}}"

    def shell(self, sandbox: SandboxBackendProtocol) -> DirectoryShell:
        return DirectoryShell(sandbox, self)

    async def record(self, thread_id: str) -> None:
        await langgraph_client().threads.update(
            thread_id=thread_id, metadata={CONTEXT_DIRECTORY_KEY: self.model_dump()}
        )

    @staticmethod
    async def forget(thread_id: str) -> None:
        """Clear the thread's directory once the checkout under it has moved."""
        await langgraph_client().threads.update(
            thread_id=thread_id, metadata={CONTEXT_DIRECTORY_KEY: None}
        )


class DirectoryShell:
    """A sandbox's shell, running each command from a context directory."""

    def __init__(self, sandbox: SandboxBackendProtocol, directory: ContextDirectory) -> None:
        self._sandbox = sandbox
        self._directory = directory

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await self._sandbox.aexecute(self._directory.command(command), timeout=timeout)


def _within(directory: str, checkout: str) -> str:
    relative = posixpath.relpath(directory, checkout)
    return "" if relative == "." else relative
