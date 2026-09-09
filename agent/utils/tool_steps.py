"""Sanitized labels and typed parsing for the tool events a run streams.

Shared by the observers that mirror a run's progress onto an external surface
(Slack Thinking Steps, Linear agent activities), so both describe the same tool
call the same way and neither leaks raw tool arguments.
"""

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any


def _text_arg(value: Any, key: str) -> str:
    if not isinstance(value, dict):
        return ""
    item = value.get(key)
    return item if isinstance(item, str) else ""


def _basename(value: str) -> str:
    return PurePath(value).name if value else "file"


def tool_step(name: str, tool_input: Any) -> tuple[str, str]:
    """A human label and a sanitized detail line for one tool call."""
    if name in {"read_file", "write_file", "edit_file", "delete"}:
        action = {
            "read_file": "Reading",
            "write_file": "Writing",
            "edit_file": "Editing",
            "delete": "Removing",
        }[name]
        return f"{action} {_basename(_text_arg(tool_input, 'file_path'))}", "Repository file"
    if name in {"glob", "grep"}:
        return "Searching repository files", "Search details hidden"
    if name in {"web_search", "fetch_url"}:
        return "Searching external documentation", "External source lookup"
    if name in {"execute", "background_execute"}:
        return "Running a development command", _text_arg(tool_input, "command")
    if name == "task":
        agent = _text_arg(tool_input, "subagent_type").replace("-", " ")
        return f"Delegating to {agent or 'a specialist'}", "Specialized agent task"
    labels = {
        "ls": ("Inspecting repository files", "Repository directory"),
        "open_pull_request": ("Opening pull request", "GitHub operation"),
        "request_pr_review": ("Starting pull request review", "GitHub operation"),
        "save_plan": ("Publishing implementation plan", "Plan artifact"),
        "analyzePlan": ("Checking implementation security", "Security analysis"),
    }
    return labels.get(name, (f"Using {name.replace('_', ' ')}", "Tool call"))


@dataclass(frozen=True)
class ToolEvent:
    namespace: tuple[str, ...]
    kind: str
    call_id: str
    tool_name: str
    tool_input: Any


def _part_value(part: Any, key: str) -> Any:
    return part.get(key) if isinstance(part, dict) else getattr(part, key, None)


def tool_event(part: Any) -> ToolEvent | None:
    """Narrow one ``runs.join_stream`` part to a tool lifecycle event, or None."""
    event = _part_value(part, "event")
    raw = _part_value(part, "data")
    if not isinstance(event, str) or not event.startswith("tools") or not isinstance(raw, dict):
        return None
    namespace = tuple(segment for segment in event.split("|")[1:] if segment)
    params = raw.get("params")
    if isinstance(params, dict):
        nested_namespace = params.get("namespace")
        if isinstance(nested_namespace, list):
            namespace = tuple(str(value) for value in nested_namespace)
        raw = params.get("data")
    if not isinstance(raw, dict):
        return None
    call_id = raw.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    kind = raw.get("event")
    tool_name = raw.get("tool_name")
    return ToolEvent(
        namespace=namespace,
        kind=kind if isinstance(kind, str) else "",
        call_id=call_id,
        tool_name=tool_name if isinstance(tool_name, str) else "",
        tool_input=raw.get("input"),
    )
