"""Admin tool for replacing the current thread's sandbox with raw create options."""

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from agent.dashboard.agent_overrides import resolve_github_login
from agent.dashboard.user_credentials import get_sandbox_langsmith_credentials
from agent.tools.admin_gate import configurable, require_admin
from agent.utils.json_types import as_json_object

logger = logging.getLogger(__name__)


class SandboxResetParams(BaseModel):
    """LangSmith sandbox create-body options."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    snapshot_id: str | None = None
    snapshot_name: str | None = None
    snapshot: str | None = None
    name: str | None = None
    timeout: int | None = None
    wait_for_ready: bool | None = None
    idle_ttl_seconds: int | None = None
    delete_after_stop_seconds: int | None = None
    vcpus: int | None = None
    cpu_millicores: int | None = None
    mem_bytes: int | None = None
    fs_capacity_bytes: int | None = None
    env_vars: dict[str, str] | None = None
    labels: dict[str, str] | None = None
    mount_config: dict[str, Any] | None = None
    proxy_config: dict[str, Any] | None = None
    preserve_memory_on_stop: bool | None = None
    restore_memory: bool | None = None
    tag_value_ids: list[str] | None = None
    internal_runtime: Any = Field(default=None, alias="_internal_runtime")


@tool("sandbox_reset", args_schema=SandboxResetParams)
async def sandbox_reset(**create_options: Any) -> dict[str, Any]:
    """Replace this admin thread's sandbox using a complete create request.

    Every supplied argument is forwarded to the LangSmith sandbox-create body.
    The schema includes all public create fields and accepts additional hidden
    fields such as ``_internal_runtime``. Omitted fields use platform defaults.
    The new sandbox starts empty except for any requested snapshot, and the old
    sandbox is preserved but detached from this thread. Never pass secrets,
    credentials, or authentication tokens.
    """
    if error := require_admin("reset sandboxes"):
        return {"success": False, "error": error}

    cfg = configurable()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    create_params = {
        ("_internal_runtime" if key == "internal_runtime" else key): value
        for key, value in create_options.items()
        if value is not None
    }
    try:
        from agent.sandboxes.lifecycle import reset_sandbox_for_thread

        login = resolve_github_login({"configurable": as_json_object(cfg.dump())})
        credentials = await get_sandbox_langsmith_credentials(login) if login else None
        kwargs = {"langsmith_credentials": credentials} if credentials is not None else {}
        old_sandbox_id, new_sandbox_id = await reset_sandbox_for_thread(
            thread_id,
            create_params,
            **kwargs,
        )
    except Exception as exc:
        logger.exception("Failed to reset sandbox for thread %s", thread_id)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "old_sandbox_id": old_sandbox_id,
        "new_sandbox_id": new_sandbox_id,
    }
