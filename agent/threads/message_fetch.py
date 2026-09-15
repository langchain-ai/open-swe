"""Fetch one message of a thread in full.

Pairs with the trimmed state view: the client paints from the trimmed
messages and asks for a single message when the user expands a stubbed tool
output or image.
"""

from fastapi import HTTPException

from agent.threads.access import _readable_thread_metadata
from agent.utils.json_types import JsonObject, as_json_object
from agent.utils.thread_ops import langgraph_client
from agent.utils.timing import phase

_MAX_MESSAGE_ID_LENGTH = 200


async def get_dashboard_thread_message(
    thread_id: str,
    message_id: str,
    login: str,
    *,
    email: str | None = None,
    timings: dict[str, float] | None = None,
) -> JsonObject:
    if not message_id or len(message_id) > _MAX_MESSAGE_ID_LENGTH:
        raise HTTPException(404, "message not found")
    record = timings if timings is not None else {}
    with phase(record, "thread_get"):
        await _readable_thread_metadata(thread_id, login=login, email=email)
    with phase(record, "get_state"):
        state = as_json_object(await langgraph_client().threads.get_state(thread_id))
    values = state.get("values")
    messages = values.get("messages") if isinstance(values, dict) else None
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict) and message.get("id") == message_id:
            return message
    raise HTTPException(404, "message not found")
