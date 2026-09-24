"""Tool: ``exit_pre_routed_mode``. Commit to a model profile and title the thread."""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

from agent.dashboard.options import available_requested_models
from agent.dashboard.team_settings import get_team_fable_enabled
from agent.model_routing import commit_route
from agent.run_config import RunConfig
from agent.utils.thread_settings import ModelRoute


async def exit_pre_routed_mode(
    model_route: ModelRoute,
    title: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    requested_model: str | None = None,
) -> Command | ToolMessage:
    """Implement the `exit_pre_routed_mode` tool."""
    cfg = RunConfig.from_runtime()
    if cfg.model_selection == "explicit":
        return ToolMessage(
            content="An explicit UI/API model choice takes precedence; do not infer a model.",
            tool_call_id=tool_call_id,
            status="error",
        )
    if requested_model is not None:
        models = available_requested_models(fable_enabled=await get_team_fable_enabled())
        if requested_model not in models:
            return ToolMessage(
                content="requested_model must be an available canonical model ID. "
                "Retry with a valid ID, or omit it if the requested model is unavailable.",
                tool_call_id=tool_call_id,
                status="error",
            )
    update = {
        "model_route": model_route,
        "pre_routed": False,
        **({"requested_model": requested_model} if requested_model is not None else {}),
    }
    if cfg.thread_id:
        update = await commit_route(
            thread_id=str(cfg.thread_id),
            cfg=cfg,
            model_route=model_route,
            title=title,
            **({"requested_model": requested_model} if requested_model is not None else {}),
        )
    return Command(
        update={
            **update,
            "messages": [
                ToolMessage(
                    content=(
                        f"Pre-routed mode is over. The rest of this thread runs on the "
                        f"{requested_model or model_route} "
                        f"{'model' if requested_model else 'profile'} with the full tool set."
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
