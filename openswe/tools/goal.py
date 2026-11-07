"""Tools: ``get_goal`` and ``update_goal``. Read and change a Goal mode thread's goal.

Offered only to people with **Goal mode** on. The agent says the work is blocked or
complete with ``report_work_state``; these read the goal and rewrite its objective,
or pause it when the person asks.
"""

from typing import Literal

from pydantic import JsonValue

from openswe.threads.goal import ThreadGoals


async def get_goal() -> dict[str, JsonValue]:
    """Implement the `get_goal` tool."""
    goal = await ThreadGoals.current(ThreadGoals.run_thread_id())
    if goal is None:
        return {"success": False, "error": "This thread has no goal; Goal mode sets one"}
    return {"success": True, **goal.model_dump(mode="json")}


async def update_goal(
    objective: str | None = None, state: Literal["paused"] | None = None
) -> dict[str, JsonValue]:
    """Implement the `update_goal` tool."""
    if objective is None and state is None:
        return {"success": False, "error": "Pass a new objective, or state to pause the goal"}
    if objective is not None and not objective.strip():
        return {"success": False, "error": "objective must not be empty"}
    thread_id = ThreadGoals.run_thread_id()
    try:
        if objective is not None:
            await ThreadGoals.rewritten(thread_id, objective.strip())
        if state == "paused":
            await ThreadGoals.paused_on_request(thread_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    current = await ThreadGoals.current(thread_id)
    return {"success": True, **({} if current is None else current.model_dump(mode="json"))}
