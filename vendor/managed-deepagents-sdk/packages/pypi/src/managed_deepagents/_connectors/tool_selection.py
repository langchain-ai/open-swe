"""Tool-name selection, shared by the connectors that load tools over MCP.

``define_mcp`` selects by raw tool name across arbitrary servers. A name in
``include_tools``/``exclude_tools`` that no loaded tool answers to is an error
naming what *was* available, so a typo fails the deployment instead of silently
dropping a tool the agent was meant to have.

Mirrors the TypeScript ``connectors/tool-selection`` module.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TypeVar

__all__ = ["read_tool_name", "select_tools"]

_ToolT = TypeVar("_ToolT")


def read_tool_name(tool: object) -> str | None:
    """Read a loaded tool's own ``name``. The default for connectors that don't rename."""
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def select_tools(
    subject: str,
    selection: Mapping[str, object],
    tools: Sequence[_ToolT],
    read_name: Callable[[object], str | None] = read_tool_name,
) -> list[_ToolT]:
    """Apply a selection to loaded tools, rejecting names that matched nothing.

    ``subject`` names the declaration in error messages, e.g. ``MCP server
    "docs"``. A tool whose name cannot be read is kept only when the selection
    is exclusive (``exclude_tools`` alone) — an ``include_tools`` list is an
    allowlist, and an unnameable tool is not on it.
    """
    include_tools = _name_list(selection.get("include_tools"))
    exclude_tools = _name_list(selection.get("exclude_tools"))
    if include_tools is None and exclude_tools is None:
        return list(tools)

    available = {name for tool in tools if (name := read_name(tool)) is not None}
    _assert_selected_tools_exist(subject, available, include_tools, exclude_tools)

    included = set(include_tools) if include_tools is not None else None
    excluded = set(exclude_tools or ())
    selected: list[_ToolT] = []
    for tool in tools:
        name = read_name(tool)
        if name is None:
            if included is None:
                selected.append(tool)
            continue
        if (included is None or name in included) and name not in excluded:
            selected.append(tool)
    return selected


def _name_list(names: object) -> list[str] | None:
    if names is None:
        return None
    if isinstance(names, str) or not isinstance(names, Iterable):
        return None
    return [name for name in names if isinstance(name, str)]


def _assert_selected_tools_exist(
    subject: str,
    available: set[str],
    include_tools: list[str] | None,
    exclude_tools: list[str] | None,
) -> None:
    selected = [*(include_tools or []), *(exclude_tools or [])]
    unknown = sorted({name for name in selected if name not in available})
    if not unknown:
        return

    available_names = ", ".join(sorted(available)) or "(none)"
    raise ValueError(
        f"{subject} selected unknown tool(s): {', '.join(unknown)}. "
        f"Available tools: {available_names}."
    )
