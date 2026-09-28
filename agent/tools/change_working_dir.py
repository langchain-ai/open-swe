"""Switch the default directory for sandbox commands in this thread."""

import posixpath
import shlex

from agent.run_config import RunConfig
from agent.sandboxes.state import get_sandbox_backend


async def change_working_dir(directory: str) -> str:
    """Set the directory for subsequent sandbox commands and load its AGENTS.md."""
    if not directory or not directory.startswith("/") or "\x00" in directory:
        raise ValueError("directory must be an absolute sandbox path")
    thread_id = RunConfig.from_runtime().thread_id
    if not thread_id:
        raise ValueError("No thread_id in current run config")
    backend = await get_sandbox_backend(thread_id)
    resolved = await backend.aexecute(f"realpath -- {shlex.quote(directory)}")
    if resolved.exit_code != 0:
        raise ValueError(f"Cannot resolve directory: {directory}")
    path = posixpath.normpath(resolved.output.strip())
    if not path.startswith("/"):
        raise ValueError("Cannot resolve absolute directory")
    checked = await backend.aexecute(f"test -d {shlex.quote(path)}")
    if checked.exit_code != 0:
        raise ValueError(f"Not a directory: {path}")
    agents_path = posixpath.join(path, "AGENTS.md")
    instructions = await backend.aread(agents_path, offset=0, limit=100_000)
    if instructions.error:
        if "not found" not in instructions.error.lower():
            raise RuntimeError(f"Cannot read {agents_path}: {instructions.error}")
        backend.change_working_directory(path)
        return f"Working directory changed to {path}. No AGENTS.md found."
    content = instructions.file_data
    if content is None or content["encoding"] != "utf-8" or instructions.next_offset:
        raise ValueError(f"Cannot read complete {agents_path} as text")
    backend.change_working_directory(path)
    return (
        f"Working directory changed to {path}. Instructions from {agents_path} "
        "(apply to subsequent work in this directory):\n\n"
        f"{content['content']}"
    )
