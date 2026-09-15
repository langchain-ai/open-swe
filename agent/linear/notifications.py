"""Deliver Linear failure notices without depending on a running agent."""

import asyncio
import logging

from langchain_core.messages import ToolMessage

from agent.tool_loaders.workspace_mcp import load_workspace_mcp_tools

logger = logging.getLogger(__name__)


async def post_linear_notification(issue_id: str, body: str) -> bool:
    """Use the workspace's selected Linear comment tool; never retry a mutation."""
    try:
        async with asyncio.timeout(30):
            tools = await load_workspace_mcp_tools(connection_name="linear")
            for name in ("save_comment", "create_comment"):
                tool = next(
                    (tool for tool in tools if (tool.metadata or {}).get("mcp_tool_name") == name),
                    None,
                )
                if tool is None:
                    continue
                result = await tool.ainvoke(
                    {
                        "name": tool.name,
                        "args": {"issueId": issue_id, "body": body},
                        "id": "linear-notification",
                        "type": "tool_call",
                    }
                )
                if isinstance(result, ToolMessage) and result.status == "success":
                    return True
                break
    except Exception:
        # Upstream exception text can contain connection credentials or comment content.
        logger.warning("Linear MCP notification failed", extra={"linear_issue_id": issue_id})
        return False

    logger.warning(
        "Linear MCP notification not delivered; check the linear connection and selected comment tool",
        extra={"linear_issue_id": issue_id},
    )
    return False
