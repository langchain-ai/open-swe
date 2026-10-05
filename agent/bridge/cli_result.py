"""The only output a bridged thread's CLI prints."""

from agent.tools.errors import ToolError

MAX_EXIT_CODE = 255


async def cli_result(stdout: str, exit_code: int) -> dict[str, bool | str]:
    """Implement the `cli_result` tool."""
    if not 0 <= exit_code <= MAX_EXIT_CODE:
        raise ToolError(f"exit_code must be between 0 and {MAX_EXIT_CODE}")
    return {"success": True}
