"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``openswe.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The follow-up queue
(``openswe.message_queue``) is retained for the dashboard's deliberate "inject a
follow-up into a run that's already in flight" path.
"""

import logging

from langgraph_sdk import get_client

from openswe.config import ENV
from openswe.message_queue import PASSIVE_KEY, QueuedContent, QueuedMessage
from openswe.utils.dashboard_handoff import ACTION_KEY

logger = logging.getLogger(__name__)


def langgraph_url() -> str:
    return ENV.LANGGRAPH_URL.get()


def langgraph_client():
    return get_client(url=langgraph_url())


async def queue_message_for_thread(thread_id: str, message_content: QueuedContent) -> bool:
    """Queue a follow-up message for a busy thread's next model call.

    Used by the dashboard to inject a follow-up into a run that's already in
    flight; webhook triggers use ``multitask_strategy="interrupt"`` instead.
    """
    queue_id = message_content.get("queue_id") if isinstance(message_content, dict) else None
    try:
        await QueuedMessage.put(
            thread_id, message_content, queue_id=queue_id if isinstance(queue_id, str) else None
        )
        logger.info("Queued message for thread %s", thread_id)
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False


async def queue_note_for_thread(thread_id: str, note: str) -> bool:
    """Queue ``note`` as context for the thread's next run without starting one."""
    return await queue_message_for_thread(thread_id, {"text": note, PASSIVE_KEY: True})


async def queue_action_for_thread(thread_id: str, login: str, action: str, note: str) -> bool:
    """Queue what ``login`` did in the app as their input to the thread's next run, starting none."""
    return await queue_message_for_thread(
        thread_id,
        {
            "text": note,
            "sender": {"id": f"github:{login}", "platform": "github", "github_login": login},
            ACTION_KEY: action,
            PASSIVE_KEY: True,
        },
    )
