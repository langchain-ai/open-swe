"""Mark a fully answered information request for delayed thread feedback."""

from typing import Any

from langgraph.config import get_config

from agent.run_config import RunConfig
from agent.thread_feedback import mark_answered_question


async def mark_question_answered() -> dict[str, Any]:
    """Mark that you are ready to deliver a complete answer to an information-only request.

    You MUST call this once before the final answer in the web UI when no
    clarification or further work is needed, then deliver the answer normally.
    This includes short questions, explanations, recommendations, and naming
    suggestions, even if no other tools were needed. Do not use for coding tasks,
    plans, approval requests, blockers, or partial answers. Coding tasks request
    feedback after their PR merges.
    For Slack answers, use slack_thread_reply with should_ask_for_feedback=True.
    Feedback is offered only if this run succeeds and the user does not continue
    the conversation for five minutes. This does not send a message or end the run.
    """
    config = get_config()
    cfg = RunConfig.from_config(config)
    if cfg.source != "dashboard":
        return {"success": False, "error": "Only dashboard runs can mark answered questions"}
    run_id = str(config.get("run_id") or cfg.run_id or "")
    if not cfg.thread_id or not run_id:
        return {"success": False, "error": "No active thread and run"}
    await mark_answered_question(cfg.thread_id, run_id)
    return {"success": True}
