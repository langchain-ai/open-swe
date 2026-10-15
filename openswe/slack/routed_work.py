from collections.abc import Sequence

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError

from openswe.database import postgres
from openswe.message_queue import QueuedMessage
from openswe.tasks.messages import TaskMessage
from openswe.tasks.store import sidebar_memberships
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY
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


async def thread_work_active(client: LangGraphClient, thread_id: str) -> bool:
    for status in ("pending", "running"):
        if await client.runs.list(thread_id, status=status, limit=1):
            return True
    metadata = thread_metadata(await client.threads.get(thread_id))
    if metadata.get(RUNNING_BACKGROUND_TASKS_KEY):
        return True
    if await TaskMessage.pending_threads([thread_id]):
        return True
    return postgres.configured() and bool(await QueuedMessage.for_thread(thread_id))


async def delegated_work_active(
    client: LangGraphClient,
    origin: str,
    *,
    visited: frozenset[str] = frozenset(),
    prune: bool = True,
) -> bool:
    if origin in visited:
        return False
    visited = visited | {origin}
    if await TaskMessage.pending_threads([origin]):
        return True
    workers = await sidebar_memberships([origin], workers_of=True)
    pending = await TaskMessage.pending_threads(list(workers))
    for worker in workers.values():
        if worker.cancelled or worker.launch_error:
            continue
        if worker.thread_id in pending:
            return True
        try:
            if await thread_work_active(client, worker.thread_id) or await delegated_work_active(
                client, worker.thread_id, visited=visited, prune=prune
            ):
                return True
        except NotFoundError:
            continue
    return await routed_work_active(client, origin, visited=visited, prune=prune)


async def routed_work_active(
    client: LangGraphClient,
    origin: str,
    *,
    visited: frozenset[str] = frozenset(),
    prune: bool = True,
) -> bool:
    metadata = thread_metadata(await client.threads.get(origin))
    active = False
    for destination in thread_ids(metadata.get(DESTINATIONS_KEY)):
        try:
            busy = await thread_work_active(client, destination) or await delegated_work_active(
                client, destination, visited=visited | {origin}, prune=prune
            )
        except NotFoundError:
            busy = False
        if busy:
            active = True
        elif prune:
            await _merge(client, origin, DESTINATIONS_KEY, remove=[destination])
            try:
                await _merge(client, destination, ORIGINS_KEY, remove=[origin])
            except NotFoundError:
                continue
    return active


async def routed_work_origins(client: LangGraphClient, destination: str) -> list[str]:
    metadata = thread_metadata(await client.threads.get(destination))
    return thread_ids(metadata.get(ORIGINS_KEY))
