"""Mark a fully answered information request for delayed thread feedback."""

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.thread_feedback import answer_marked_for_run, mark_answered_question


async def mark_question_answered() -> dict[str, object]:
    """Mark an answered request; repeats are no-ops, so proceed directly to the final answer."""
    config = get_config()
    cfg = RunConfig.from_config(config)
    run_id = str(config.get("run_id") or cfg.run_id or "")
    if not cfg.thread_id or not run_id:
        return {"success": False, "error": "No active thread and run"}
    if await answer_marked_for_run(cfg.thread_id, run_id):
        return {
            "success": True,
            "already_marked": True,
            "note": "This request is already marked answered. Do not call this tool again; deliver your final answer now.",
        }
    await mark_answered_question(cfg.thread_id, run_id)
    return {"success": True}
