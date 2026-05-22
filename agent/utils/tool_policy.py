"""Runtime tool and webhook policy helpers.

The upstream project exposes several integration tools by default. Northstar's
testrepo-first profile keeps those integrations present in source but disables
them at runtime through explicit environment policy.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def parse_csv_env(name: str) -> frozenset[str]:
    """Parse a comma-separated env var into lowercase non-empty tokens."""
    return frozenset(
        item.strip().lower() for item in os.environ.get(name, "").split(",") if item.strip()
    )


def get_tool_name(tool: object) -> str:
    """Best-effort tool name for LangChain tools or plain callables."""
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    name = getattr(tool, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return tool.__class__.__name__


def filter_disabled_tools(
    tools: Iterable[T], disabled_names: Iterable[str] | None = None
) -> list[T]:
    """Return tools whose names are not listed in DISABLED_AGENT_TOOLS."""
    disabled = (
        frozenset(name.strip().lower() for name in disabled_names if name.strip())
        if disabled_names is not None
        else parse_csv_env("DISABLED_AGENT_TOOLS")
    )
    if not disabled:
        return list(tools)
    return [tool for tool in tools if get_tool_name(tool).lower() not in disabled]


def is_webhook_disabled(name: str) -> bool:
    """Return whether a webhook family is disabled by DISABLED_WEBHOOKS."""
    return name.strip().lower() in parse_csv_env("DISABLED_WEBHOOKS")
