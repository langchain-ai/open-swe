"""Authorized Omnia task and approval actions for Luna."""

import hashlib
import json
import uuid
from typing import Any, Literal

from langgraph.config import get_config

from ..utils.omnia import post_omnia_agent_action


async def omnia_agent_action(
    action: Literal["list_tasks", "create_task", "merge_task", "browser_session"],
    title: str | None = None,
    task_number: int | None = None,
    preview_url: str | None = None,
    redirect_path: str | None = None,
) -> dict[str, Any]:
    """Read/create tasks, authenticate a preview browser, or execute an approved merge.

    Use create_task only when the human clearly hands Luna new coding work. Use merge_task
    only after a clear approval for that exact task in this Omnia conversation. Use
    browser_session only for the exact Vercel preview being visually verified.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return {"success": False, "error": "Missing run configuration"}
    omnia_thread = configurable.get("omnia_thread")
    if not isinstance(omnia_thread, dict):
        return {"success": False, "error": "Missing Omnia conversation"}
    if action == "create_task" and not (isinstance(title, str) and title.strip()):
        return {"success": False, "error": "title is required for create_task"}
    if action == "merge_task" and not isinstance(task_number, int):
        return {"success": False, "error": "task_number is required for merge_task"}
    if action == "browser_session" and not (
        isinstance(preview_url, str) and preview_url.startswith("https://")
    ):
        return {"success": False, "error": "preview_url is required for browser_session"}
    run_id = config.get("run_id") or configurable.get("run_id") or "unknown-run"
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "action": action,
                "title": title,
                "task_number": task_number,
                "preview_url": preview_url,
                "redirect_path": redirect_path,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]
    if action == "browser_session":
        fingerprint = uuid.uuid4().hex
    result = await post_omnia_agent_action(
        {
            "action": action,
            "title": title.strip() if isinstance(title, str) else None,
            "task_number": task_number,
            "preview_url": preview_url,
            "redirect_path": redirect_path,
            "dm_thread_id": omnia_thread.get("thread_id"),
            "sender_email": configurable.get("user_email"),
            "agent_thread_id": configurable.get("thread_id"),
            "idempotency_key": f"open-swe:{run_id}:{fingerprint}",
        }
    )
    if action == "create_task" and result.get("success"):
        task = result.get("task")
        number = task.get("taskNumber") if isinstance(task, dict) else None
        journal = omnia_thread.get("journal_run_id")
        if isinstance(number, int) and isinstance(journal, int):
            result["branch_name"] = f"agent/luna/task-{number}-run-{journal}"
    return result
