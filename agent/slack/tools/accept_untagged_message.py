import asyncio
from typing import Any

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.slack.client import get_active_slack_thread, set_slack_thread_status
from agent.slack.thinking import maintain_slack_thinking_status
from agent.utils.thread_ops import langgraph_client

_THINKING_STATUS = "Thinking..."
_status_tasks: set[asyncio.Task[None]] = set()


async def slack_accept_untagged_message() -> dict[str, Any]:
    """Accept an untagged Slack message and display the thinking status."""
    config = get_config()
    cfg = RunConfig.from_config(config)
    if not cfg.untagged_reply:
        return {"success": False, "error": "This run was explicitly requested"}
    if not cfg.thread_id or not cfg.run_id:
        return {"success": False, "error": "Missing thread_id or run_id in config"}

    client = langgraph_client()
    active = await get_active_slack_thread(
        client,
        cfg.thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
    )
    active = active or {}
    channel_id = active.get("channel_id")
    thread_ts = active.get("thread_ts")
    if not channel_id or not thread_ts:
        return {
            "success": False,
            "error": "Missing slack_thread.channel_id or slack_thread.thread_ts in config",
        }
    if not await set_slack_thread_status(channel_id, thread_ts, _THINKING_STATUS):
        return {"success": False, "error": "Could not display Slack thinking status"}

    task = asyncio.create_task(
        maintain_slack_thinking_status(
            client=client,
            thread_id=cfg.thread_id,
            run_id=cfg.run_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
    )
    _status_tasks.add(task)
    task.add_done_callback(_status_tasks.discard)
    return {"success": True}
