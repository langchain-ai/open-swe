"""Tell the agent the person handed its browser back, with a run dispatch starts.

MDA's ``browser_handoff`` gives the person the thread's browser window and returns at
once. When they hand it back, or their link runs out, MDA enqueues a run on the
thread with a notification. Only dispatch signs a run token, so MDA hands that
run to the ``wake`` hook, as the backend delivers a background task's completion.
"""

from typing import Any, cast

from langgraph_sdk import get_client

from openswe.background_tasks import completion_configurable
from openswe.dispatch import dispatch_agent_run
from openswe.input_messages import InputMessageContext, SystemIdentity, build_run_input
from openswe.utils.thread_ops import langgraph_url

_HANDOFF_SENDER: SystemIdentity = {
    "id": "system:browser-handoff",
    "display_name": "Browser",
    "platform": "open-swe",
}
_HANDOFF_CONTEXT: InputMessageContext = {
    "sender_id": _HANDOFF_SENDER["id"],
    "surface": "automation",
    "kind": "system",
}


async def wake_after_handoff(thread_id: str, text: str, notification: dict[str, Any]) -> None:
    """Continue ``thread_id`` for its owner with MDA's notification, enqueued.

    The message keeps MDA's ``mda_notification`` marker, so MDA ends the handoff
    when the run starts and clients tell it from the person's messages.
    """
    thread = await get_client(url=langgraph_url()).threads.get(thread_id)
    configurable = await completion_configurable(thread_id, thread.get("metadata") or {})
    run_input = build_run_input(text, _HANDOFF_CONTEXT, systems=[_HANDOFF_SENDER])
    cast("dict[str, Any]", run_input["messages"][-1])["mda_notification"] = notification
    await dispatch_agent_run(
        thread_id,
        None,
        configurable,
        source=str(configurable.get("source") or "dashboard"),
        thread_title=None,
        input=run_input,
        metadata={},
        multitask_strategy="enqueue",
    )
