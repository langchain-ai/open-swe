"""Resolve personal integration access and PR authorship within saved thread scope."""

from collections.abc import Mapping
from typing import Any

import langgraph_sdk

from agent.run_config import RunConfig
from agent.utils.json_types import thread_metadata


async def _thread_scope(
    config: Mapping[str, Any] | None = None, *, thread_id: str | None = None
) -> tuple[RunConfig, dict[str, Any]]:
    cfg = RunConfig.from_config(config) if config is not None else RunConfig.from_runtime()
    thread_id = thread_id or cfg.thread_id
    if not thread_id:
        raise RuntimeError("Cannot resolve credential scope without a thread_id")
    thread = await langgraph_sdk.get_client().threads.get(thread_id)
    metadata = thread_metadata(thread)
    visibility = metadata.get("visibility", "public")
    if visibility not in ("public", "private"):
        raise RuntimeError("Cannot resolve credentials for an unknown thread visibility")
    owner_type = metadata.get("owner_type")
    if owner_type not in (None, "user", "system"):
        raise RuntimeError("Cannot resolve credentials for an unknown thread owner type")
    if owner_type == "system" and visibility != "public":
        raise RuntimeError("System threads cannot use private credentials")
    return cfg, metadata


def _private_owner_login(cfg: RunConfig, metadata: Mapping[str, Any]) -> str:
    owner = metadata.get("owner_login")
    if not isinstance(owner, str) or not owner.strip():
        raise RuntimeError("Private thread has no credential owner")
    owner = owner.strip().lower()
    login = (cfg.github_login or "").strip()
    if owner != login.lower():
        raise RuntimeError("Personal credentials require the private thread owner to start the run")
    return login


async def private_credential_login(
    config: Mapping[str, Any] | None = None, *, thread_id: str | None = None
) -> str | None:
    """Personal integrations require the saved private owner to start the run."""
    cfg, metadata = await _thread_scope(config, thread_id=thread_id)
    if metadata.get("visibility", "public") == "public":
        return None
    return _private_owner_login(cfg, metadata)


async def pr_author_login() -> str | None:
    """Use the authenticated run requester for PRs in user-owned threads."""
    cfg, metadata = await _thread_scope()
    owner = metadata.get("owner_login")
    has_owner = isinstance(owner, str) and bool(owner.strip())
    if (
        cfg.background_task_completion
        and metadata.get("owner_type") != "system"
        and (
            metadata.get("visibility") == "private"
            or metadata.get("owner_type") == "user"
            or has_owner
        )
    ):
        raise RuntimeError(
            "Background completion cannot identify the PR requester; start a direct user run to publish"
        )
    if metadata.get("visibility", "public") == "private":
        return _private_owner_login(cfg, metadata)
    if metadata.get("owner_type") == "system":
        return None
    if has_owner:
        login = (cfg.github_login or "").strip()
        if not login:
            raise RuntimeError(
                "User-owned thread requires an authenticated requester for PR creation"
            )
        return login
    if metadata.get("owner_type") == "user":
        raise RuntimeError("User-owned thread has no GitHub owner for PR creation")
    return None


async def thread_owner_login(config: Mapping[str, object]) -> str | None:
    """Resolve the saved owner for private and user-started workspace threads."""
    _, metadata = await _thread_scope(config)
    owner = metadata.get("owner_login")
    if metadata.get("owner_type") == "system":
        return None
    return owner.strip() if isinstance(owner, str) and owner.strip() else None
