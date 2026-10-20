"""Native async local commands with bounded output and process-tree cleanup."""

import asyncio
import logging
import os
import signal
from typing import TYPE_CHECKING

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from agent.prompts import render_prompt

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent.sandboxes.windows_job import WindowsJob


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
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("Use aexecute")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command.strip():
            raise ValueError("command must be nonempty")
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
