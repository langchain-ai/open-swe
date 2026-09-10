"""Open SWE's sandbox lifecycle: the generic lifecycle wired to this platform."""

from agent.dashboard.environments import resolve_environment
from agent.dashboard.sandbox_settings import get_admin_base_snapshot_id
from agent.sandboxes.credentials import GitHubProxyCredentials
from agent.utils.authorship import OPEN_SWE_BOT_EMAIL, OPEN_SWE_BOT_NAME
from coding_agent.sandboxes.git_identity import GitIdentity
from coding_agent.sandboxes.lifecycle import SandboxCreateConfig, SandboxLifecycle


async def resolve_open_swe_create_config(
    environment_slug: str | None = None,
) -> SandboxCreateConfig:
    """Boot a new sandbox from the environment's snapshot, else the base snapshot."""
    environment = await resolve_environment(environment_slug)
    if environment is None:
        return SandboxCreateConfig(snapshot_id=await get_admin_base_snapshot_id())
    return SandboxCreateConfig(
        snapshot_id=environment.ready_snapshot_id or await get_admin_base_snapshot_id(),
        resources=environment.sandbox_resources(),
        create_params=environment.sandbox_create_params(),
    )


OPEN_SWE_SANDBOXES = SandboxLifecycle(
    resolve_create_config=resolve_open_swe_create_config,
    credentials=GitHubProxyCredentials(),
    git_identity=GitIdentity(OPEN_SWE_BOT_NAME, OPEN_SWE_BOT_EMAIL),
)

ensure_sandbox_for_thread = OPEN_SWE_SANDBOXES.ensure_for_thread
get_cached_sandbox_backend = OPEN_SWE_SANDBOXES.cached_backend
reset_sandbox_for_thread = OPEN_SWE_SANDBOXES.reset_for_thread
recreate_sandbox_for_thread = OPEN_SWE_SANDBOXES.recreate_for_thread
