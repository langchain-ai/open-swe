"""Submit explicit user feedback for the active thread."""

from typing import Annotated, Literal, TypedDict

from langchain.tools import ToolRuntime
from pydantic import Field

from agent.middleware.model_selection import ModelSelectionState, Route
from agent.run_config import RunConfig
from agent.thread_feedback import Feedback, feedback_store
from agent.utils.langsmith import create_langsmith_thread_feedback
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock


class ThreadFeedbackResult(TypedDict):
    status: Literal["completed"]
    rating: Literal["bad", "good"]
    comment: str
    export_status: Literal["exported", "saved_without_export"]


async def submit_thread_feedback(
    rating: Literal["bad", "good"],
    runtime: ToolRuntime[None, ModelSelectionState],
    comment: Annotated[str, Field(max_length=3000)] = "",
) -> ThreadFeedbackResult:
    """Submit feedback explicitly stated by the user about this thread."""
    cfg = RunConfig.from_config(runtime.config)
    if not cfg.thread_id:
        raise ValueError("No active thread")
    normalized_comment = comment.strip()
    run_id = str(runtime.config.get("run_id") or cfg.run_id or "")
    route: Route | None = runtime.state.get("model_route") if runtime.state else None
    async with agent_thread_pr_state_lock(langgraph_client(), cfg.thread_id):
        record = await feedback_store().get(cfg.thread_id)
        record = record or Feedback(event_id=f"tool:{run_id}", answer_run_id=run_id)
        record.status = "completed"
        record.rating = rating
        record.comment = normalized_comment
        await feedback_store().put(cfg.thread_id, record)
    exported = await create_langsmith_thread_feedback(
        cfg.thread_id,
        "rating",
        score=1.0 if rating == "good" else 0.0,
        comment=normalized_comment or None,
        source_info={
            "source": "agent_thread_feedback_tool",
            "run_id": run_id,
            **({"model_route": route} if route else {}),
        },
    )
    return ThreadFeedbackResult(
        status="completed",
        rating=rating,
        comment=normalized_comment,
        export_status="exported" if exported else "saved_without_export",
    )
