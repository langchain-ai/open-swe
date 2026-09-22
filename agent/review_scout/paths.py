import re

from deepagents.backends.protocol import SandboxBackendProtocol

from agent.run_config import RunConfig
from agent.sandboxes.paths import resolve_sandbox_work_dir

_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


async def scout_repo_dir(backend: SandboxBackendProtocol, cfg: RunConfig) -> str | None:
    """Where the scout's checkout of the PR's repository lives, or ``None`` without one."""
    repo_name = cfg.repo.name if cfg.repo else ""
    if not _REPO_NAME_RE.fullmatch(repo_name):
        return None
    return f"{await resolve_sandbox_work_dir(backend)}/{repo_name}"
