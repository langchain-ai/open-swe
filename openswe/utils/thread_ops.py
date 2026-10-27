"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``openswe.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The follow-up queue
(``openswe.message_queue``) is retained for the dashboard's deliberate "inject a
follow-up into a run that's already in flight" path.
"""

from langgraph_sdk import get_client

from openswe.config import ENV


def langgraph_url() -> str:
    return ENV.LANGGRAPH_URL.get()


def langgraph_client():
    return get_client(url=langgraph_url())
