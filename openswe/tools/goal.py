"""Tools: ``goal_create``, ``goal_get``, ``goal_set_objective`` and ``goal_set_state``.
Set, read and change a thread's goal.

Anyone can ask for a goal (``goal_create``); a person with Goal mode by default gets one
from every request. While a goal is active the thread's runs work in Goal mode and keep
going until it waits on its person or pauses, or a wait will wake the thread again.
None of these tools says anything to the person: the agent replies as on any other turn.
"""

import logging
from typing import Final, Literal

from langgraph.config import get_config
from pydantic import JsonValue
from runtime_api.loop import GoalState

from openswe.agent_api.tokens import AGENT_TURN_KEY
from openswe.dispatch import dispatch_agent_run
from openswe.input_messages import SystemIdentity
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.tasks.store import UserWaitReason
from openswe.threads.goal import ThreadGoals

logger = logging.getLogger(__name__)

_GOAL_SENDER: Final[SystemIdentity] = {
    "id": "system:goal",
    "display_name": "Goal",
    "platform": "open-swe",
}
# What the run that takes the goal up carries over from the run that set it.
_CARRIED_CONFIG: Final = (
    "auth_provider",
    "user_id",
    "repo",
    "source",
    "slack_thread",
    "linear_issue",
    "github_login",
    "user_email",
    "workspace",
)


async def goal_create(objective: str) -> dict[str, JsonValue]:
    """Implement the `goal_create` tool."""
    if not objective.strip():
        return {"success": False, "error": "objective must not be empty"}
    cfg = RunConfig.from_runtime()
    thread_id = ThreadGoals.run_thread_id()
    try:
        created = await ThreadGoals.created(thread_id, objective.strip())
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    current = await ThreadGoals.current(thread_id)
    shown = None if current is None else current.model_dump(mode="json")
    if not created:
        return {
            "success": False,
            "error": "This thread's goal is underway; change it with goal_set_objective",
            "goal": shown,
        }
    turn = get_config().get("configurable", {}).get(AGENT_TURN_KEY)
    if isinstance(turn, dict) and turn.get("goal_mode") is True:
        return {"success": True, **(shown or {})}
    # This run started outside Goal mode: the one queued behind it works toward the goal.
    dumped = cfg.dump()
    try:
        await dispatch_agent_run(
            thread_id,
            prompt("runs/goal-started"),
            {
                "thread_id": thread_id,
                **{key: dumped[key] for key in _CARRIED_CONFIG if dumped.get(key) is not None},
            },
            source=cfg.source or "dashboard",
            thread_title=None,
            context={"sender_id": _GOAL_SENDER["id"], "surface": "automation", "kind": "system"},
            systems=[_GOAL_SENDER],
            multitask_strategy="enqueue",
        )
    except Exception:
        # The run may still have been queued; if it was, waking the paused goal resumes it.
        logger.exception("Could not start the run for a new goal", extra={"thread_id": thread_id})
        await ThreadGoals.failed(thread_id)
        return {
            "success": False,
            "error": "The goal's run may not have started",
            "next_step": prompt("runs/goal-dispatch-failed"),
        }
    return {"success": True, **(shown or {}), "next_step": prompt("runs/goal-created")}


async def goal_get() -> dict[str, JsonValue]:
    """Implement the `goal_get` tool."""
    goal = await ThreadGoals.current(ThreadGoals.run_thread_id())
    if goal is None:
        return {"success": False, "error": "This thread has no goal; goal_create sets one"}
    return {"success": True, **goal.model_dump(mode="json")}


async def goal_set_objective(objective: str) -> dict[str, JsonValue]:
    """Implement the `goal_set_objective` tool."""
    if not objective.strip():
        return {"success": False, "error": "objective must not be empty"}
    thread_id = ThreadGoals.run_thread_id()
    try:
        await ThreadGoals.rewritten(thread_id, objective.strip())
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return await goal_get()


async def goal_set_state(
    expected_state: GoalState,
    state: Literal["working", "waiting_on_user", "paused", "complete"],
    reason: UserWaitReason | None = None,
) -> dict[str, JsonValue]:
    """Implement the `goal_set_state` tool."""
    if (state == "waiting_on_user") != (reason is not None):
        return {
            "success": False,
            "error": "Pass reason (blocked or ready) with waiting_on_user, and only with it",
        }
    thread_id = ThreadGoals.run_thread_id()
    try:
        outcome = await ThreadGoals.set_state(thread_id, expected_state, state, reason)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    current = await ThreadGoals.current(thread_id)
    shown = None if current is None else current.model_dump(mode="json")
    if outcome == "check":
        return {
            "success": False,
            "error": "Check your work before completing the goal",
            "next_step": prompt("runs/goal-completion-check"),
            "goal": shown,
        }
    if outcome == "stale":
        return {
            "success": False,
            "error": f"The goal is no longer {expected_state}, so it was left as it is",
            "goal": shown,
        }
    return {"success": True, **(shown or {})}
