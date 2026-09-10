from typing import Any

from agent.store import delete_value, put_value, search_values

THREAD_PINS_NAMESPACE = "thread_pins"


def _namespace(login: str) -> list[str]:
    return [THREAD_PINS_NAMESPACE, login]


async def list_thread_pin_ids(login: str) -> list[str]:
    records = await search_values(_namespace(login), limit=1000)
    return [
        thread_id
        for record in records
        if isinstance((thread_id := record.get("thread_id")), str) and thread_id
    ]


async def pin_thread(login: str, thread_id: str) -> dict[str, Any]:
    record = {"thread_id": thread_id}
    await put_value(_namespace(login), thread_id, record)
    return record


async def unpin_thread(login: str, thread_id: str) -> None:
    await delete_value(_namespace(login), thread_id)
