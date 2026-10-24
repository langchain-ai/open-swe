"""Record that this thread should be checked after its pull request deploys."""

from openswe.rollout_events import ROLLOUT_CHECK_CONTEXT, ROLLOUT_CHECK_REQUESTED
from openswe.run_config import RunConfig
from openswe.utils.thread_ops import langgraph_client


async def request_rollout_check() -> dict[str, object]:
    """Implement the `request_rollout_check` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}
    await langgraph_client().threads.update(
        thread_id=thread_id,
        metadata={
            ROLLOUT_CHECK_REQUESTED: True,
            ROLLOUT_CHECK_CONTEXT: {
                "source": cfg.source,
                "sender_id": cfg.sender_id,
                "slack_thread": cfg.slack_thread.dump()
                if cfg.source == "slack" and cfg.slack_thread
                else None,
            },
        },
    )
    return {"success": True, "rollout_check": True}
