"""Read-only LangGraph Store access for private admin surfaces."""

import logging

from agent.store import get_value
from agent.tools.access import Policy, access
from agent.tools.mcp_exposure import expose_mcp

logger = logging.getLogger(__name__)


@expose_mcp(access="admin")
@access(Policy(trusted="admin_surface", actor="admin"))
async def read_store_item(namespace: list[str], key: str) -> dict[str, object]:
    """Read one LangGraph Store item on a private admin surface."""
    if not namespace or any(not part for part in namespace) or not key:
        return {"ok": False, "error": "Namespace components and key cannot be empty."}
    try:
        value = await get_value(namespace, key)
    except Exception:
        logger.exception("Admin store item read failed")
        return {"ok": False, "error": "Could not read the LangGraph Store item."}
    return {
        "ok": True,
        "namespace": namespace,
        "key": key,
        "found": value is not None,
        "value": value,
    }
