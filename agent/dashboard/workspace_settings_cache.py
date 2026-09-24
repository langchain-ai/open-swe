"""Read canonical shared settings without a second, worker-local copy."""

from agent.dashboard.workspace_settings import (
    WorkspaceSettings,
    get_workspace_settings,
    resolve_settings_workspace,
)


async def cached_workspace_settings(workspace: str | None) -> WorkspaceSettings:
    slug = resolve_settings_workspace(workspace)
    return await get_workspace_settings(slug)
