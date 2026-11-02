"""Tool: ``report_work_state``. Say the work is blocked or complete, with the reply that says so.

Offered only to people with **Goal mode** on, whose runs keep going until
this call or a wait that wakes the thread again.
"""

from typing import Annotated, Any, Literal

from langgraph.prebuilt import InjectedState

from openswe.middleware.require_user_reply import SLACK_REPLY_SURFACE, current_reply_surface
from openswe.prompts import prompt
from openswe.slack.tools.reply import send_slack_reply
from openswe.threads.goal import ThreadGoal


async def report_work_state(
    state: Literal["blocked", "complete"],
    message: str,
    run_state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Implement the `report_work_state` tool."""
    if not message.strip():
        return {"success": False, "error": "message is required: it is the reply the person gets"}
    # Recorded first: a failure after the reply posts would have the agent post it again.
    await ThreadGoal.of_run().reported(state, message)
    if current_reply_surface(run_state or {}) == SLACK_REPLY_SURFACE:
        result = await send_slack_reply(message, "final", state=run_state)
        return {**result, "work_state": state} if result.get("success") else result
    return {
        "success": True,
        "work_state": state,
        "next_step": prompt("runs/work-state-web-reply"),
    }
