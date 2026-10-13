"""Record that this thread should be checked after its pull request deploys."""

from openswe.rollout_events import (
    ROLLOUT_CHECK_REQUESTED,
    merged_pull_request,
    subscribe_merged_thread,
)
from openswe.run_config import RunConfig
from openswe.utils.thread_ops import langgraph_client


async def request_rollout_check() -> dict[str, object]:
    """Implement the `request_rollout_check` tool."""
    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}
    client = langgraph_client()
    current = await client.threads.get(thread_id)
    metadata = current.get("metadata") if isinstance(current, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    await client.threads.update(
        thread_id=thread_id,
        metadata={ROLLOUT_CHECK_REQUESTED: True},
    )
    identity = merged_pull_request(metadata)
    if identity is None:
        return {"success": True, "rollout_check": True, "subscribed": False}
    owner, repo, number, sha = identity
    await subscribe_merged_thread(
        thread_id,
        owner=owner,
        repo=repo,
        number=number,
        sha=sha,
        metadata={**metadata, ROLLOUT_CHECK_REQUESTED: True},
    )
    return {"success": True, "rollout_check": True, "subscribed": True}
