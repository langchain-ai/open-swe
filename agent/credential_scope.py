"""Personal credentials are available only to a private thread's owner."""

from collections.abc import Mapping
from typing import Any

import langgraph_sdk

from agent.run_config import RunConfig
from agent.utils.json_types import thread_metadata


async def private_credential_login(
    config: Mapping[str, Any] | None = None, *, thread_id: str | None = None
) -> str | None:
    """Verify saved visibility and ownership; never trust caller-supplied visibility."""
    cfg = RunConfig.from_config(config) if config is not None else RunConfig.from_runtime()
    thread_id = thread_id or cfg.thread_id
    if not thread_id:
        raise RuntimeError("Cannot resolve credential scope without a thread_id")
    thread = await langgraph_sdk.get_client().threads.get(thread_id)
    metadata = thread_metadata(thread)
    visibility = metadata.get("visibility", "public")
    if visibility == "public":
        return None
    if visibility != "private":
        raise RuntimeError("Cannot resolve credentials for an unknown thread visibility")
    owner = metadata.get("owner_login")
    if not isinstance(owner, str) or not owner.strip():
        raise RuntimeError("Private thread has no credential owner")
    owner = owner.strip().lower()
    login = (cfg.github_login or "").strip()
    if owner != login.lower():
        raise RuntimeError("Personal credentials require the private thread owner to start the run")
    return login
