from collections.abc import Sequence

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError

from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

DESTINATIONS_KEY = "routed_work_destinations"
ORIGINS_KEY = "routed_work_origins"


def thread_ids(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


async def _merge(
    client: LangGraphClient,
    thread_id: str,
    key: str,
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
) -> None:
    async with agent_thread_pr_state_lock(client, thread_id):
        metadata = thread_metadata(await client.threads.get(thread_id))
        current = set(thread_ids(metadata.get(key)))
        updated = sorted((current | set(add)) - set(remove))
        if updated != sorted(current):
            await client.threads.update(thread_id, metadata={key: updated})


async def track_routed_work(client: LangGraphClient, origin: str, destination: str) -> None:
    if origin == destination:
        return
    await _merge(client, destination, ORIGINS_KEY, add=[origin])
    await _merge(client, origin, DESTINATIONS_KEY, add=[destination])


async def routed_work_active(client: LangGraphClient, origin: str) -> bool:
    metadata = thread_metadata(await client.threads.get(origin))
    active = False
    for destination in thread_ids(metadata.get(DESTINATIONS_KEY)):
        try:
            busy = any(
                [
                    await client.runs.list(destination, status=status, limit=1)
                    for status in ("pending", "running")
                ]
            )
        except NotFoundError:
            busy = False
        if busy:
            active = True
        else:
            await _merge(client, origin, DESTINATIONS_KEY, remove=[destination])
            try:
                await _merge(client, destination, ORIGINS_KEY, remove=[origin])
            except NotFoundError:
                continue
    return active


async def routed_work_origins(client: LangGraphClient, destination: str) -> list[str]:
    metadata = thread_metadata(await client.threads.get(destination))
    return thread_ids(metadata.get(ORIGINS_KEY))
