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
_ABSOLUTE_PATH = re.compile(r"""(?<![A-Za-z0-9_])([A-Za-z]:[\\/][^"'|<>;&]*|[\\/]{2}[^"'|<>;&]+)""")


def _resolve(path: str | Path) -> str:
    """A comparable form of a path that may not exist yet.

    `realpath` resolves symlinks only for the leading part that exists and keeps
    the rest lexically, so a not-yet-created target such as the checkout a
    command is about to clone still compares against its parent.
    """
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _outside_sandbox(command: str, roots: Sequence[Path]) -> str | None:
    """The first absolute path in the command that is outside every root.

    A shell cannot be confined by inspecting its command: the same tree is
    reachable through an interpreter, a variable, or a glob. This only catches
    the plain absolute paths a model writes when it wanders out of the project
    (`type D:\\work\\open-swe\\artifacts\\...`), which is what left eval runs
    reading their own expected patches. Containment is therefore a guardrail
    against drift, not a security boundary.
    """
    normalized_roots = [_resolve(root) for root in roots]
    for match in _ABSOLUTE_PATH.finditer(command):
        candidate = match.group(1).rstrip()
        if not candidate:
            continue
        normalized = _resolve(candidate)
        if not any(
            normalized == root or normalized.startswith(root + os.sep) for root in normalized_roots
        ):
            return candidate
    return None


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

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("Use aexecute")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command.strip():
            raise ValueError("command must be nonempty")
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
