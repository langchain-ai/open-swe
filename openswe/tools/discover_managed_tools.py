import logging

from openswe.credential_scope import private_credential_login
from openswe.dashboard.langsmith_oauth import LangSmithOAuthError
from openswe.mcp.managed import ManagedToolsError, gateway_inventory, instance_gateway

logger = logging.getLogger(__name__)


async def discover_managed_tools(query: str = "") -> dict[str, object]:
    """Discover the private owner's available LangSmith Managed Tools and missing connections."""
    try:
        login = await private_credential_login()
        if login is None:
            return {
                "success": False,
                "error": "Personal managed tools require a private dashboard thread or Slack DM",
            }
        gateway = await instance_gateway()
        if gateway is None:
            return {
                "success": False,
                "error": "No managed tools gateway is configured; ask an instance admin to select one",
            }
        inventory = await gateway_inventory(login, gateway)
    except (RuntimeError, ManagedToolsError) as exc:
        return {"success": False, "error": str(exc)}
    except LangSmithOAuthError as exc:
        logger.warning(
            "Managed tools discovery authentication failed", extra={"status_code": exc.status_code}
        )
        return {"success": False, "error": "LangSmith authentication failed; reconnect in Settings"}
    key = query.strip().casefold()
    tools = [
        tool
        for tool in inventory.tools
        if not key or key in tool.name.casefold() or key in (tool.description or "").casefold()
    ]
    return {
        "success": True,
        "gateway": inventory.gateway.model_dump(),
        "tools": [tool.model_dump() for tool in tools],
        "total_available_tools": len(inventory.tools),
        "missing": [item.model_dump() for item in inventory.missing],
        "all_required": inventory.all_required,
    }
