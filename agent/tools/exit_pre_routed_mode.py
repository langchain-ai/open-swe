"""Tool: ``exit_pre_routed_mode``. Commit to a model profile and title the thread."""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

from agent.model_routing import commit_route
from agent.run_config import RunConfig
from agent.utils.thread_settings import ModelRoute


async def exit_pre_routed_mode(
    model_route: ModelRoute,
    title: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Implement the `exit_pre_routed_mode` tool."""
    cfg = RunConfig.from_runtime()
    update = {"model_route": model_route, "pre_routed": False}
    if cfg.thread_id:
        update = await commit_route(
            thread_id=str(cfg.thread_id), cfg=cfg, model_route=model_route, title=title
        )
    return Command(
        update={
            **update,
            "messages": [
                ToolMessage(
                    content=(
                        f"Pre-routed mode is over. The rest of this thread runs on the "
                        f"{model_route} profile with the full tool set."
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
