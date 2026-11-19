"""Move everyone's installs of an app to one of its releases, through an authorized admin write."""

from uuid import UUID

from openswe.audit_logs.tools import audit_tool
from openswe.plugins.records import AppNotFound, PluginApps, PluginInstallations
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users.models import User

_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("app", "release"))


@audit_tool()
@expose_mcp(access="admin")
@access(_WRITE)
async def upgrade_app_installs(app: str, release: int | None = None) -> dict[str, object]:
    """Move everyone's installs of an app to one of its releases."""
    cfg = RunConfig.from_runtime()
    admin = (
        await User.get(UUID(cfg.user_id))
        if cfg.user_id
        else await User.for_login("github", cfg.github_login)
        if cfg.github_login
        else None
    )
    if admin is None:
        raise ValueError("Only a person signed in to Robin can move everyone's installs")
    try:
        version = await PluginApps.numbered_release(UUID(app.strip()), release)
    except AppNotFound as exc:
        raise ValueError("That app has no such release") from exc
    moved = await PluginInstallations.move_all(version, by=admin.id)
    return {
        "app": app,
        "release": version.number,
        "moved": moved.moved,
        "current": moved.current,
        "waiting": moved.waiting,
    }
