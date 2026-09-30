"""Adapt personal LangSmith credentials to the shared, fresh-per-call MCP runtime."""

from langchain_core.tools import BaseTool

from agent.credential_scope import private_credential_login
from agent.langsmith_connection.credentials import load
from agent.mcp.models import MCPConnection
from agent.mcp.runtime import MCPSource, load_mcp_tools


def source(login: str) -> MCPSource:
    async def get_connection(name: str) -> MCPConnection | None:
        owner = await private_credential_login()
        if name != "personal_langsmith" or owner is None or owner.lower() != login.lower():
            return None
        return await load(owner)

    async def list_connections() -> list[MCPConnection]:
        record = await get_connection("personal_langsmith")
        return [record] if record else []

    return MCPSource(
        namespace=("langsmith", login.lower()),
        list_connections=list_connections,
        get_connection=get_connection,
    )


async def load_tools(login: str | None) -> list[BaseTool]:
    return await load_mcp_tools(source(login)) if login else []
