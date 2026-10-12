"""Sandbox tool API schemas."""

from typing import Literal

from pydantic import BaseModel, JsonValue, RootModel


class ToolDescription(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class ToolArguments(RootModel[dict[str, JsonValue]]):
    pass


class ToolResult(BaseModel):
    status: Literal["success", "error"]
    content: JsonValue
