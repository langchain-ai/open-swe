"""Sandbox tool API schemas."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from openswe.mcp.models import MCPToolProvenance


class ToolDescription(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]
    mcp_provenance: MCPToolProvenance | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ToolResult(BaseModel):
    status: Literal["success", "error"]
    content: JsonValue
