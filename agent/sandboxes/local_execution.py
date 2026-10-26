"""Native async local commands with bounded output and process-tree cleanup."""

import asyncio
import logging
import os
import re
import signal
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import LocalShellBackend
from deepagents.backends.local_shell import DEFAULT_EXECUTE_TIMEOUT
from deepagents.backends.protocol import ExecuteResponse

from agent.prompts import render_prompt

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent.sandboxes.windows_job import WindowsJob

# An absolute Windows path, optionally quoted, as a model writes one in a shell
# command. Drive-relative forms ("D:foo") are not matched: `os.path` reads those
# as "on drive D, relative to its current directory", not as a real location.
# A URL's "//" is not an UNC path, and a clone URL is on nearly every command.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S*")
_ABSOLUTE_PATH = re.compile(r"""(?<![A-Za-z0-9_])([A-Za-z]:[\\/][^"'|<>;&]*|[\\/]{2}[^"'|<>;&]+)""")


# Commands that reach the network, and the upstream hosts an evaluation must
# not consult: the issue an eval task is built from has a public resolution,
# and a model that finds it is answering a different question than the one the
# benchmark asks. Cloning the task repository itself is how every task starts,
# so that one form stays allowed.
_NETWORK_COMMAND = re.compile(
    r"""(?<![A-Za-z0-9_])(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)(?![A-Za-z0-9_])""",
    re.IGNORECASE,
)
_GIT_REMOTE = re.compile(
    r"""\bgit\b[^|;&\n]*?\b(clone|fetch|pull|remote|ls-remote)\b""", re.IGNORECASE
)
_UPSTREAM_HOST = re.compile(r"""\b(api\.github\.com|github\.com|raw\.githubusercontent\.com)\b""")
_ANY_URL = re.compile(r"""[A-Za-z][A-Za-z0-9+.-]*://[^\s"'|<>;&]+""")
_CLONE_TARGET = re.compile(r"""\bgit\b[^|;&\n]*?\bclone\b""", re.IGNORECASE)


_STATEMENT = re.compile(r"&&|\|\||[;|\n]")

# `cd ..` / `cd /d "path"`: the shell's working directory moves, and every
# relative path after it is judged from where it moved to.
_CHANGE_DIRECTORY = re.compile(
    r"""(?:^|\b)(?:cd|chdir|pushd)\b(?:\s+/d)?\s+("[^"]*"|'[^']*'|[^\s"']+)""", re.IGNORECASE
)
# A token that climbs out of its directory, as `..` or a path containing it.
_STEP_UP = re.compile(r"(?:^|[\\/])\.\.(?:$|[\\/])")


def _network_escape(command: str) -> str | None:
    """The part of a command that would reach the network, if any.

    Only the checkout step is permitted: the task prompt tells the model to
    clone its repository, and `git fetch`/`git pull` after that would bring in
    the upstream fix the task asks it to write. A statement is judged on its
    own, so a clone followed by a lookup does not smuggle the lookup through.
    """
    for statement in _STATEMENT.split(command):
        if not statement.strip():
            continue
        if _CLONE_TARGET.search(statement) and not _NETWORK_COMMAND.search(statement):
            continue
        remote = _GIT_REMOTE.search(statement)
        if remote is not None:
            return remote.group(0).strip()
        network = _NETWORK_COMMAND.search(statement)
        if network is not None:
            return network.group(0)
        for url in _ANY_URL.findall(statement):
            if _UPSTREAM_HOST.search(url):
                return url
    return None


def _resolve(path: str | Path) -> str:
    """A comparable form of a path that may not exist yet.

    `realpath` resolves symlinks only for the leading part that exists and keeps
    the rest lexically, so a not-yet-created target such as the checkout a
    command is about to clone still compares against its parent.
    """
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _within(path: str, roots: Sequence[str]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in roots)


def _outside_sandbox(command: str, roots: Sequence[Path]) -> str | None:
    """The first path in the command that leaves every root.

    A shell cannot be confined by inspecting its command: the same tree is
    reachable through an interpreter, a variable, or a glob. This only catches
    the plain paths a model writes when it wanders out of the project, which is
    what left eval runs reading their own expected patches. Containment is
    therefore a guardrail against drift, not a security boundary.

    Absolute paths are checked as written. Relative ones are checked against
    the directory the command will run in, because `dir ..` reaches the parent
    just as surely as the full path does, and the workspace a checkout is cloned
    into keeps its siblings one `..` away.
    """
    normalized_roots = [_resolve(root) for root in roots]
    cleaned = _SCHEME.sub(" ", command)
    for match in _ABSOLUTE_PATH.finditer(cleaned):
        candidate = match.group(1).rstrip()
        if not candidate:
            continue
        if not _within(_resolve(candidate), normalized_roots):
            return candidate
    base = Path(roots[0])
    for statement in _STATEMENT.split(cleaned):
        stripped = statement.strip()
        change = _CHANGE_DIRECTORY.match(stripped)
        if change is not None:
            target = next((group for group in change.groups() if group), None)
            if target:
                target = target.strip("\"'")
                resolved = _resolve(Path(base) / target)
                if not _within(resolved, normalized_roots):
                    return target
                base = Path(resolved)
                stripped = stripped[change.end() :]
        for path in _relative_paths(stripped):
            if not _within(_resolve(Path(base) / path), normalized_roots):
                return path
    return None


def _relative_paths(statement: str) -> list[str]:
    """Relative path tokens in a statement that step up out of their directory."""
    paths: list[str] = []
    for token in re.findall(r""""[^"]*"|'[^']*'|[^\s"']+""", statement):
        value = token.strip("\"'")
        if value and _STEP_UP.search(value):
            paths.append(value)
    return paths


async def _read_output(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    content = bytearray()
    truncated = False
    while chunk := await stream.read(65536):
        available = max(0, limit - len(content))
        content.extend(chunk[:available])
        truncated = truncated or len(chunk) > available
    return bytes(content), truncated


async def _terminate_tree(process: asyncio.subprocess.Process, job: WindowsJob | None) -> None:
    if os.name == "nt":
        if job is not None:
            await job.aclose()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                logger.debug("Local command already exited", extra={"process_id": process.pid})
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            logger.debug("Local command process group exited", extra={"process_id": process.pid})
    await process.wait()


class AsyncLocalShellBackend(LocalShellBackend):
    """Local shell access confined to a project plus readable toolchain roots.

    `extra_roots` are directories a command may also name by absolute path,
    typically the interpreters and system tools a build needs. Leaving it empty
    makes the project the only absolute location a command may mention.
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        virtual_mode: bool = True,
        timeout: int = DEFAULT_EXECUTE_TIMEOUT,
        max_output_bytes: int = 100_000,
        env: dict[str, str] | None = None,
        inherit_env: bool = False,
        extra_roots: Sequence[str] = (),
        offline: bool = False,
    ) -> None:
        super().__init__(
            root_dir,
            virtual_mode=virtual_mode,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            env=env,
            inherit_env=inherit_env,
        )
        self._extra_roots = [Path(root) for root in extra_roots]
        self._offline = offline

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("Use aexecute")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command.strip():
            raise ValueError("command must be nonempty")
        if self._offline:
            escape = _network_escape(command)
            if escape is not None:
                logger.warning("Blocked a command that reaches the network")
                return ExecuteResponse(
                    output=render_prompt("tools/local-command-offline.md", command=escape),
                    exit_code=126,
                    truncated=False,
                )
        outside = _outside_sandbox(command, [self.cwd, *self._extra_roots])
        if outside is not None:
            logger.warning("Blocked a command naming a path outside the sandbox")
            return ExecuteResponse(
                output=render_prompt("tools/local-command-outside-sandbox.md", path=outside),
                exit_code=126,
                truncated=False,
            )
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            raise ValueError("timeout must be positive")
        job: WindowsJob | None = None
        if os.name == "nt":
            from agent.sandboxes.windows_job import WindowsJob

            job = WindowsJob()
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=self.cwd,
                env=self._env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
        except BaseException:
            if job is not None:
                await job.aclose()
            raise
        if process.stdout is None:
            raise RuntimeError("Local command stdout was not connected")
        reader = asyncio.create_task(_read_output(process.stdout, self._max_output_bytes))
        timed_out = False
        finished = False
        try:
            if job is not None and process.returncode is None:
                await job.attach(process.pid)
            async with asyncio.timeout(effective_timeout):
                await process.wait()
                content, truncated = await asyncio.shield(reader)
                finished = True
        except TimeoutError:
            timed_out = True
            await _terminate_tree(process, job)
            content, truncated = await reader
            logger.warning("Local command timed out", extra={"timeout_seconds": effective_timeout})
        except BaseException:
            await _terminate_tree(process, job)
            await reader
            raise
        finally:
            if job is not None:
                await job.aclose(terminate=not finished)
        output = content.decode("utf-8", errors="replace")
        if truncated:
            output += "\n" + render_prompt(
                "tools/local-output-truncated.md", limit=self._max_output_bytes
            )
        if timed_out:
            output += "\n" + render_prompt(
                "tools/local-command-timeout.md", timeout=effective_timeout
            )
        return ExecuteResponse(
            output=output,
            exit_code=124 if timed_out else process.returncode,
            truncated=truncated,
        )
