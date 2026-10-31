import logging
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from openswe.middleware.require_user_reply import SLACK_REPLY_SURFACE, RequireUserReplyMiddleware
from openswe.prompts import prompt
from openswe.threads.store import THREADS
from openswe.utils.background_task_state import RUNNING_MONITORS_KEY

logger = logging.getLogger(__name__)

REQUIRED_CONFIRMATION = "The user cannot see anything I do not send to Slack."


def _normalized(text: str) -> str:
    return " ".join(text.split()).rstrip(".").casefold()


async def slack_no_reply_needed(
    reason: str,
    confirmation: str,
    state: Annotated[dict[str, object] | None, InjectedState] = None,
) -> dict[str, object]:
    """Implement the `slack_no_reply_needed` tool."""
    stated = reason.strip()
    if not stated:
        return {
            "success": False,
            "error": "reason is required",
            "hint": "State in one sentence why this turn warrants no reply.",
        }
    if _normalized(confirmation) != _normalized(REQUIRED_CONFIRMATION):
        return {
            "success": False,
            "error": "confirmation does not match",
            "hint": (
                f"Type exactly: {REQUIRED_CONFIRMATION} If the asker is waiting on anything, "
                "send it with `slack_reply` instead."
            ),
        }
    configurable = get_config().get("configurable", {})
    state = state or {}
    listed = state.get("messages")
    messages = (
        [message for message in listed if isinstance(message, BaseMessage)]
        if isinstance(listed, list)
        else []
    )
    guard = RequireUserReplyMiddleware(
        "slack_reply", "slack_no_reply_needed", initial_surface=SLACK_REPLY_SURFACE
    )
    if guard.requester_still_owed(messages):
        tasks = state.get("async_tasks")
        resumable = isinstance(tasks, dict) and any(
            isinstance(task, dict) and task.get("status") in {"pending", "running"}
            for task in tasks.values()
        )
        thread_id = configurable.get("thread_id")
        if not resumable and isinstance(thread_id, str):
            thread = await THREADS.get(thread_id)
            resumable = bool(thread and thread.metadata.get(RUNNING_MONITORS_KEY))
        if not resumable:
            return {
                "success": False,
                "error": "requester_still_owed",
                "hint": (
                    'Send the result or blocker through slack_reply with response_type="final", '
                    "or start a monitor when waiting on a process. "
                    "An unmonitored process will not resume delivery."
                ),
            }
    logger.info(
        "Turn declared as needing no user-facing reply",
        extra={
            "agent_thread_id": configurable.get("thread_id"),
            "no_reply_reason": stated,
        },
    )
    return {"success": True, "turn_closed": True, "next_step": prompt("runs/slack-turn-closed")}
