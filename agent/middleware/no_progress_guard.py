import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentState, ToolCallRequest, hook_config
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt

_REPEAT_NUDGE_THRESHOLD = 3
_REPEAT_TERMINATION_THRESHOLD = 6
_LIMIT_MARKER = "Model call limits exceeded"
_STATE_CHANGING_TOOLS = frozenset(
    {
        "edit_file",
        "write_file",
        "open_pull_request",
        "cli_result",
        "slack_reply",
        "slack_thread_reply",
        "record_incident_report",
    }
)
_GIT_STATE_CHANGE = re.compile(r"\bgit\s+(?:-[^\s]+\s+)*(?:commit|merge|revert|cherry-pick)\b")
_NO_PROGRESS_INSTRUCTION = load_prompt("no-progress-guard.md")


def _canonical(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _result_digest(result: object) -> str:
    if isinstance(result, ToolMessage):
        result = {
            "content": result.content,
            "artifact": result.artifact,
            "status": result.status,
        }
    return hashlib.sha256(_canonical(result).encode()).hexdigest()


def _tool_name(request: ToolCallRequest) -> str:
    return str(request.tool_call.get("name", ""))


def _is_state_changing(name: str, args: Mapping[str, object]) -> bool:
    if name in _STATE_CHANGING_TOOLS:
        return True
    command = args.get("command")
    return (
        name in {"execute", "background_execute"}
        and isinstance(command, str)
        and bool(_GIT_STATE_CHANGE.search(command))
    )


class NoProgressGuardMiddleware(OpenSWEMiddleware):
    """Stop a run that repeats an identical read-only tool call."""

    def __init__(self) -> None:
        super().__init__()
        self._last_call: tuple[str, str, str] | None = None
        self._repeat_count = 0
        self._nudge_pending = False

    def _reset(self) -> None:
        self._last_call = None
        self._repeat_count = 0
        self._nudge_pending = False

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        if self._repeat_count >= _REPEAT_TERMINATION_THRESHOLD:
            return {
                "jump_to": "end",
                "messages": [AIMessage(content=f"{_LIMIT_MARKER}: repeated read-only work")],
            }
        if self._nudge_pending:
            self._nudge_pending = False
            return {"messages": [SystemMessage(content=_NO_PROGRESS_INSTRUCTION.strip())]}
        return None

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        name = _tool_name(request)
        args = request.tool_call.get("args", {})
        if not isinstance(args, Mapping) or _is_state_changing(name, args):
            self._reset()
            return result

        call = (name, _canonical(args), _result_digest(result))
        if call == self._last_call:
            self._repeat_count += 1
        else:
            self._last_call = call
            self._repeat_count = 1
        if self._repeat_count == _REPEAT_NUDGE_THRESHOLD:
            self._nudge_pending = True
        return result
