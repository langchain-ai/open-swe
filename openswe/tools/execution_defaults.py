"""Authorized access to Robin-wide instructions and sandbox defaults."""

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope"))


@expose_mcp(access="admin")
@access(_READ)
async def read_execution_defaults() -> dict[str, object]:
    """Read the Robin-wide instructions and sandbox configuration under Defaults."""
    return (await EXECUTION_DEFAULTS.load()).model_dump(mode="json")


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def configure_execution_defaults(update: ExecutionDefaults) -> dict[str, object]:
    """Save supplied Robin-wide defaults for future work without recreating sandboxes."""
    saved = await EXECUTION_DEFAULTS.update(update)
    return {"scope": "execution", "settings": saved.model_dump(mode="json")}
