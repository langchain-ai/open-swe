"""The agent's own routing decision: persisted per thread, final once made."""

from typing import Any

from langgraph_sdk import get_client

from agent.run_config import RunConfig
from agent.thread_title import name_thread
from agent.utils.thread_settings import ModelRoute, store_thread_model_route


async def commit_route(
    *,
    thread_id: str,
    cfg: RunConfig,
    model_route: ModelRoute,
    title: str,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """Persist the route and title for the thread; returns the run-state update."""
    await name_thread(thread_id=thread_id, title=title, cfg=cfg)
    await store_thread_model_route(
        get_client(), thread_id, model_route, requested_model=requested_model
    )
    return {
        "model_route": model_route,
        "pre_routed": False,
        **({"requested_model": requested_model} if requested_model is not None else {}),
    }
