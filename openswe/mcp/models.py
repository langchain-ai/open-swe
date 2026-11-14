"""MCP servers as the runtime connects to them, whichever source names them."""

from typing import Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt

# ``managed``: a LangSmith Managed Tools kit.
type MCPScope = Literal["managed"]


class MCPToolProvenance(BaseModel):
    """Credential-free provenance from the caller's authorized connection sources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_name: str
    scope: MCPScope
    overridden_scopes: list[MCPScope]

    def note(self) -> str:
        """What the description of each tool from this connection ends with, for the model."""
        return prompt("tools/mcp-provenance", provenance=self.model_dump_json())


class MCPConnection(BaseModel):
    """One MCP server: where it is, which of its tools runs get, and how it authenticates."""

    model_config = ConfigDict(hide_input_in_errors=True)

    name: str
    url: str
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    # What the server's cached tool list is kept under; a new one lists its tools again.
    revision: str

    def allows_tool(self, name: str) -> bool:
        return name in self.allowed_tools

    async def check_response(self, response: httpx2.Response) -> None:
        """Raise for a response the MCP transport would reduce to a bare protocol error."""
        return

    def unusable_tools(self, meta: object) -> set[str]:
        """Tools a listing names but marks, in its ``_meta``, as unusable for the caller."""
        return set()

    @property
    def offers_tools(self) -> bool:
        return bool(self.allowed_tools)

    def connection_headers(self) -> dict[str, str]:
        """The headers the backend sends the server, carrying the caller's credential."""
        return {}
