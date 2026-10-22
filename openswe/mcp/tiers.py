"""The MCP tools a run may use, combined from every connection tier it is entitled to."""

from langchain_core.tools import BaseTool

from openswe.mcp.instance import instance_mcp_source
from openswe.mcp.managed import managed_mcp_source
from openswe.mcp.runtime import load_mcp_tools
from openswe.mcp.user import user_mcp_source
from openswe.mcp.workspace import workspace_mcp_source


async def mcp_tools_for(
    credential_login: str | None, workspace: str, managed_gateway: str | None
) -> list[BaseTool]:
    """Load the run's MCPs by tier: instance, then workspace, then the user's own.

    A later tier's connection replaces a same-named one from the tier before. The
    instance's LangSmith Managed Tools gateway comes last, used as the private owner.
    Without a credential login only the shared instance and workspace tiers load.
    """
    sources = [instance_mcp_source(), workspace_mcp_source(workspace)]
    if credential_login:
        sources.append(user_mcp_source(credential_login))
        if managed_gateway:
            sources.append(managed_mcp_source(credential_login, managed_gateway))
    return await load_mcp_tools(*sources)
