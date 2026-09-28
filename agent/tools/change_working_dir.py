"""Switch the default directory for sandbox commands in this thread."""

import json
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
    script = (
        "python3 -c "
        + shlex.quote(
            "import json, os, pathlib, sys; "
            "path = pathlib.Path(sys.argv[1]).resolve(strict=True); "
            "path.is_dir() or sys.exit('Not a directory'); "
            "agents = path / 'AGENTS.md'; "
            "print(json.dumps({'directory': str(path), "
            "'instructions': agents.read_text(encoding='utf-8') if agents.exists() else None}))"
        )
        + " "
        + shlex.quote(directory)
    )
    result = await backend.aexecute(script)
    if result.exit_code != 0:
        raise ValueError(result.output.strip() or f"Cannot change working directory to {directory}")
    data = json.loads(result.output)
    path = data["directory"]
    instructions = data["instructions"]
    backend.change_working_directory(path)
    if instructions is None:
        return f"Working directory changed to {path}. No AGENTS.md found."
    return (
        f"Working directory changed to {path}. Instructions from {path}/AGENTS.md "
        "(apply to subsequent work in this directory):\n\n"
        f"{instructions}"
    )
