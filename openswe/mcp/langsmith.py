"""LangSmith MCP tools act in the workspace each call names, never a default one.

LangSmith's MCP runs a call without ``workspace_id`` in its credential's default
workspace, so a resource in another workspace answers 403 or not found, which reads
as missing access. Open SWE requires the workspace on every LangSmith tool that takes
one, and says which workspace a LangSmith access error came from.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self
from urllib.parse import urlsplit
from uuid import UUID

from langchain_core.tools import ToolException

from mcp.types import Tool
from openswe.config import ENV
from openswe.prompts import prompt

_WORKSPACE = "workspace_id"
_LANGSMITH_DOMAIN = "smith.langchain.com"
# Managed Tools proxies third-party servers from LangSmith's host; their workspaces aren't LangSmith's.
_MANAGED_TOOLS_PATH = "/v1/managed-tools/"
_ACCESS_ERROR = re.compile(r"\b40[134]\b|forbidden|not found|permission", re.IGNORECASE)


@dataclass(frozen=True)
class WorkspaceScopedTool:
    """A LangSmith MCP tool whose calls must each name the workspace they act in."""

    definition: Tool

    @classmethod
    def of(cls, definition: Tool, url: str) -> Self | None:
        """``definition`` when it takes a workspace and LangSmith's own MCP at ``url`` serves it."""
        properties = definition.input_schema.get("properties")
        if not isinstance(properties, dict) or _WORKSPACE not in properties:
            return None
        return cls(definition) if _langsmith_mcp(url) else None

    @property
    def input_schema(self) -> dict[str, object]:
        schema = self.definition.input_schema
        required = schema.get("required")
        return {
            **schema,
            "properties": {
                **schema["properties"],
                _WORKSPACE: {
                    "type": "string",
                    "description": prompt("tools/langsmith-workspace-id"),
                },
            },
            "required": [
                *(name for name in required or () if name != _WORKSPACE),
                _WORKSPACE,
            ],
        }

    def check(self, arguments: Mapping[str, object]) -> None:
        """Refuse a call before LangSmith sees it unless it names a workspace UUID."""
        value = arguments.get(_WORKSPACE)
        try:
            UUID(value if isinstance(value, str) else "")
        except ValueError:
            raise ToolException(
                prompt("tools/langsmith-workspace-required", tool=self.definition.name)
            ) from None

    def explain(self, error: ToolException, arguments: Mapping[str, object]) -> ToolException:
        """``error``, naming the call's workspace when LangSmith refused or found nothing."""
        message = str(error)
        if not _ACCESS_ERROR.search(message):
            return error
        note = prompt("tools/langsmith-workspace-denied", workspace=arguments[_WORKSPACE])
        return ToolException(f"{message}\n\n{note}")


def _langsmith_mcp(url: str) -> bool:
    """Whether ``url`` is LangSmith's own MCP: on LangSmith's domain or its configured API host."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None or _MANAGED_TOOLS_PATH in parsed.path:
        return False
    configured = {
        urlsplit(origin).hostname
        for origin in (ENV.LANGSMITH_ENDPOINT.get(), ENV.LANGSMITH_CONNECTION_URL.get())
    }
    return host == _LANGSMITH_DOMAIN or host.endswith(f".{_LANGSMITH_DOMAIN}") or host in configured
