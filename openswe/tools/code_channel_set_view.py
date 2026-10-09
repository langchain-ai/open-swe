"""Tool: ``code_channel_set_view``. Creates or replaces a view in the run's Slack code channel."""

from typing import Any

from openswe.run_config import RunConfig
from openswe.slack.client import get_active_slack_thread
from openswe.slack.code_channels import (
    CanvasAccessLevel,
    ViewType,
    is_code_channel_session,
    set_view,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.tools.manage_code_channel import resolve_view_content
from openswe.utils.thread_ops import langgraph_client


async def code_channel_set_view(
    view_type: ViewType,
    view_key: str = "",
    name: str = "",
    content: str = "",
    file_path: str = "",
    blocks: list[dict[str, Any]] | None = None,
    canvas_id: str = "",
    access_level: CanvasAccessLevel = "write",
    base_branch: str = "",
    head_branch: str = "",
    csp: dict[str, list[str]] | None = None,
    agent_content_hash: str = "",
) -> dict[str, Any]:
    """Implement the `code_channel_set_view` tool."""
    cfg = RunConfig.from_runtime()
    if not cfg.thread_id:
        return {"success": False, "error": "Missing thread_id in config"}
    active = await get_active_slack_thread(
        langgraph_client(), cfg.thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
    )
    if not active or not is_code_channel_session(str(active.get("thread_ts") or "")):
        return {"success": False, "error": "this conversation is not in a Slack code channel"}
    try:
        data = await set_view(
            str(active.get("channel_id") or ""),
            view_type,
            view_key=view_key,
            content=await resolve_view_content(content, file_path),
            blocks=blocks,
            canvas_id=canvas_id,
            access_level=access_level,
            base_branch=base_branch,
            head_branch=head_branch,
            name=name,
            csp=csp,
            agent_content_hash=agent_content_hash,
        )
    except (ValueError, SlackRequestError) as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "slack_response": data}
