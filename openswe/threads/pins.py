"""The threads each person pinned to the top of their dashboard sidebar."""

import logging

from openswe.database.store_imports import StoreImport
from openswe.store import delete_value, search_all_entries
from openswe.users.records import UnknownUser, UserRecords

logger = logging.getLogger(__name__)

LEGACY_THREAD_PINS_NAMESPACE = "thread_pins"
THREAD_PINS = UserRecords("thread_pin")


async def list_thread_pin_ids(login: str) -> list[str]:
    """The person's pinned thread IDs, oldest pin first."""
    return await THREAD_PINS.keys(login)


async def pin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.add(login, {}, key=thread_id)


async def unpin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.delete(login, key=thread_id)


async def import_store_pins() -> StoreImport:
    """Move pins, kept under each person's login in the LangGraph Store, into ``user_record``.

    A pin whose login has no ``users`` row stays in the Store for the next startup.
    """
    moved = waiting = 0
    for entry in await search_all_entries([LEGACY_THREAD_PINS_NAMESPACE]):
        namespace = entry.namespace
        if namespace is None or len(namespace) != 2:
            logger.warning(
                "Skipping unexpected thread pin namespace", extra={"namespace": namespace}
            )
            continue
        thread_id = entry.value.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            try:
                await pin_thread(namespace[1], thread_id)
            except UnknownUser:
                logger.warning(
                    "Thread pin waits for its users row", extra={"github_login": namespace[1]}
                )
                waiting += 1
                continue
        await delete_value(namespace, entry.key)
        moved += 1
    return StoreImport(moved, waiting)
