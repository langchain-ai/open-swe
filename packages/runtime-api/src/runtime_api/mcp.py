"""The MCP servers a run reaches itself, rather than through the backend's operations.

The catalog names each server and the tools the run may call on it
(``Catalog.mcp_servers``); ``MCP_HEADERS`` hands the run their headers, which never
ride the catalog. The backend still serves every other connection as operations.
"""

from typing import Literal

from pydantic import BaseModel

from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest


class McpServerSpec(BaseModel):
    """One MCP connection the run calls directly."""

    # The connection's name; its tools are named ``mcp_{name}_{tool}``.
    name: str
    url: str
    transport: Literal["http", "sse"]
    # The tools the connection allows that the server still offers, by their raw names.
    tools: list[str]


class McpHeaders(BaseModel):
    # Each server's request headers, by its name.
    servers: dict[str, dict[str, str]] = {}


# Only reads, so it is safe to send again.
MCP_HEADERS = Endpoint(
    "mcp_headers", RunRequest, McpHeaders, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
