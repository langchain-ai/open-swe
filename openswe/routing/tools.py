"""Authorized agent tools for individual integration bindings."""

from uuid import UUID

from openswe.audit_logs.tools import audit_tool
from openswe.routing.store import INTEGRATIONS, ChannelMode
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User

_READ = Policy(trusted="admin_thread", actor="admin")
_WRITE = Policy(trusted="admin_thread", actor="admin", sole=ack())


class RoutingActor:
    @staticmethod
    async def id() -> UUID | None:
        cfg = RunConfig.from_runtime()
        if cfg.user_id:
            return UUID(cfg.user_id)
        user = await User.for_login("github", cfg.github_login) if cfg.github_login else None
        return user.id if user is not None else None


@expose_mcp(access="admin")
@access(_READ)
async def list_integration_bindings() -> dict[str, object]:
    """List instance repository bindings and Slack channel modes."""
    return {"ok": True, **(await INTEGRATIONS.settings()).model_dump(mode="json")}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def bind_repository(repo: str, may_start_threads: bool = False) -> dict[str, object]:
    """Bind a repository; workflow thread creation requires its separate explicit grant."""
    record = await INTEGRATIONS.configure_repository(
        repo,
        may_start_threads=may_start_threads,
        actor=await RoutingActor.id(),
    )
    return {"ok": True, **record.model_dump(mode="json")}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def unbind_repository(repo: str) -> dict[str, object]:
    """Remove a repository binding only when no queue configuration or history depends on it."""
    try:
        await INTEGRATIONS.remove_repository(repo)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def bind_slack_channel(channel_id: str, mode: ChannelMode) -> dict[str, object]:
    """Bind a Slack channel in mentions, kitchen or ask mode after live validation."""
    record = await INTEGRATIONS.configure_channel(
        channel_id,
        mode=mode,
        actor=await RoutingActor.id(),
    )
    return {"ok": True, **record.model_dump(mode="json")}


@expose_mcp(access="admin")
@audit_tool()
@access(_WRITE)
async def unbind_slack_channel(channel_id: str) -> dict[str, object]:
    """Remove one Slack channel binding; personal kitchen preferences remain independent."""
    await INTEGRATIONS.remove_channel(channel_id)
    return {"ok": True}
