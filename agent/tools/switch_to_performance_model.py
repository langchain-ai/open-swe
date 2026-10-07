"""Explicit performance-tier model switching for the current thread."""

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.types import Command

from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.run_config import RunConfig
from agent.slack.client import post_slack_ephemeral_message
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_settings import load_thread_settings, store_thread_settings


async def switch_to_performance_model(
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Switch the current thread to its performance-tier model."""
    cfg = RunConfig.from_config(get_config())
    if not cfg.thread_id:
        raise ValueError("No current agent thread is available")
    client = langgraph_client()
    settings = (await load_thread_settings(client, cfg.thread_id)).copy()
    performance = settings.get("routing_models", {}).get("performance")
    if performance:
        model_id, effort = performance["model_id"], performance["effort"]
    else:
        defaults = await cached_workspace_settings(cfg.workspace or cfg.environment)
        model_id, effort = defaults.agent_routing_models["performance"]
    settings.update(
        model_id=model_id,
        effort=effort,
        requested_model=model_id,
        model_handoff_complete=True,
        model_routing_enabled=False,
    )
    await store_thread_settings(client, cfg.thread_id, settings, strict=True)
    confirmation = f"Switched to {model_id} (reasoning effort: {effort or 'default'})."
    if cfg.slack_thread and cfg.slack_thread.triggering_user_id:
        await post_slack_ephemeral_message(
            cfg.slack_thread.channel_id,
            cfg.slack_thread.triggering_user_id,
            confirmation,
            thread_ts=cfg.slack_thread.reply_thread_ts or cfg.slack_thread.thread_ts,
        )
    return Command(
        update={
            "requested_model": model_id,
            "requested_effort": effort,
            "messages": [ToolMessage(content=confirmation, tool_call_id=tool_call_id)],
        }
    )
