import logging

from openswe.credential_scope import private_credential_login
from openswe.dashboard.langsmith_oauth import LangSmithOAuthError
from openswe.mcp.managed import ManagedToolsError, PersonalTools

logger = logging.getLogger(__name__)


async def discover_managed_tools(query: str = "") -> dict[str, object]:
    """List the managed tool kit's services, connected by the private owner or not."""
    try:
        login = await private_credential_login()
        if login is None:
            return {
                "success": False,
                "error": "Personal managed tools require a private dashboard thread or Slack DM",
            }
        servers = await PersonalTools(login).kit_servers()
    except (RuntimeError, ManagedToolsError) as exc:
        return {"success": False, "error": str(exc)}
    except LangSmithOAuthError as exc:
        logger.warning(
            "Managed tools discovery authentication failed", extra={"status_code": exc.status_code}
        )
        return {"success": False, "error": "LangSmith authentication failed; reconnect in Settings"}
    key = query.strip().casefold()
    listed = [server for server in servers if server.enabled and (not key or server.matches(key))]
    return {
        "success": True,
        "connected": [{"name": server.name} for server in listed if server.usable],
        "connectable": [
            {"id": server.id, "name": server.name, "kind": server.kind}
            for server in listed
            if server.needs_connect
        ],
    }
