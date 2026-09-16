"""Tool: ``add_repository``. Records another repository on the agent thread."""

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import HTTPException
from langgraph.prebuilt import InjectedState
from langgraph_sdk import get_client

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.run_config import Repo, RunConfig
from agent.thread_repos import (
    REPOS_METADATA_KEY,
    repos_metadata,
    thread_repos,
    with_thread_repo,
)
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_url
from agent.webhooks.common import is_repo_allowed

logger = logging.getLogger(__name__)


async def _repo_instructions(repo: Repo) -> str | None:
    from agent.dashboard.agent_instructions import get_repo_agent_instructions

    try:
        return await get_repo_agent_instructions(repo.owner, repo.name)
    except Exception:
        logger.debug("Failed to load repo custom agent instructions", exc_info=True)
        return None


async def add_repository(
    full_name: str,
    state: Annotated[Mapping[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Add a GitHub repository to this thread's repository list.

    Use this when the task requires working in a repository the thread does not
    list yet. The repository is recorded on the thread, is cloned beside the
    others under the working directory, and is included in later prompts and
    diffs. Pass ``owner/name``.
    """
    repo = Repo.parse_full_name(full_name)
    if repo is None:
        return {
            "ok": False,
            "error": "full_name must be a simple owner/name repository string",
        }

    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"ok": False, "error": "No thread_id in the current run config"}

    if not is_repo_allowed({"owner": repo.owner, "name": repo.name}):
        return {
            "ok": False,
            "error": f"Repository {repo.full_name} is not on the deployment allowlist",
        }

    github_login = (cfg.github_login or "").strip()
    if not github_login:
        return {
            "ok": False,
            "error": (f"Cannot verify access to {repo.full_name}: this thread has no github_login"),
        }
    try:
        await require_repo_access_for_user(github_login, repo.full_name)
    except HTTPException as exc:
        return {
            "ok": False,
            "error": f"Access to repository {repo.full_name} denied: {exc.detail}",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to verify repository access",
            extra={"repository_full_name": repo.full_name},
            exc_info=True,
        )
        return {
            "ok": False,
            "error": f"Failed to verify access to repository {repo.full_name}: {exc}",
        }

    client = get_client(url=langgraph_url())
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to read thread repositories",
            extra={"repository_full_name": repo.full_name},
            exc_info=True,
        )
        return {"ok": False, "error": f"Failed to read this thread's repositories: {exc}"}

    metadata = thread_metadata(thread)
    repos = with_thread_repo(metadata, repo)
    already_present = len(repos) == len(thread_repos(metadata))
    if not already_present:
        try:
            await client.threads.update(
                thread_id=thread_id,
                metadata={REPOS_METADATA_KEY: repos_metadata(repos)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to record thread repository",
                extra={"repository_full_name": repo.full_name},
                exc_info=True,
            )
            return {"ok": False, "error": f"Failed to record {repo.full_name}: {exc}"}

    result: dict[str, Any] = {
        "ok": True,
        "repos": [entry.full_name for entry in repos],
        "already_present": already_present,
        "instructions": await _repo_instructions(repo),
    }
    work_dir = state.get("work_dir") if isinstance(state, Mapping) else None
    if isinstance(work_dir, str) and work_dir:
        result["clone_hint"] = f"cd {work_dir} && gh repo clone {repo.full_name}"
    return result
