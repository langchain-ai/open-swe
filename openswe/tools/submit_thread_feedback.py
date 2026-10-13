"""Submit explicit user feedback for the active thread."""

from typing import Annotated, Literal, TypedDict

from langchain.tools import ToolRuntime
from pydantic import Field

from openswe.audit_logs.tools import audit_tool
from openswe.middleware.model_selection import ModelSelectionState, SelectedRoute, normalize_route
from openswe.run_config import RunConfig
from openswe.thread_feedback import record_thread_feedback


class ThreadFeedbackResult(TypedDict):
    status: Literal["completed"]
    rating: Literal["bad", "good"]
    comment: str
    export_status: Literal["exported", "saved_without_export"]


@audit_tool()
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
    persisted_route = runtime.state.get("model_route") if runtime.state else None
    route: SelectedRoute | None = normalize_route(persisted_route) if persisted_route else None
    exported = await record_thread_feedback(
        cfg.thread_id,
        rating,
        normalized_comment,
        event_id=f"tool:{run_id}",
        run_id=run_id,
        source=cfg.source or "unknown",
        source_info={
            "source": "agent_thread_feedback_tool",
            "run_id": run_id,
            **({"model_route": route} if route else {}),
        },
        github_login=cfg.github_login,
        user_email=cfg.user_email,
        slack_user_id=(cfg.slack_thread.triggering_user_id if cfg.slack_thread else None),
    )
    return ThreadFeedbackResult(
        status="completed",
        rating=rating,
        comment=normalized_comment,
        export_status="exported" if exported else "saved_without_export",
    )
