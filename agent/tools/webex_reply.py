import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.webex import post_webex_message


def webex_reply(message: str) -> dict[str, Any]:
    """Post a message to the current Webex thread.

    Uses standard Markdown formatting: **bold**, *italic*, [link](url),
    bullet lists with "- ", ```code blocks```, > blockquotes."""
    config = get_config()
    configurable = config.get("configurable", {})
    webex_thread = configurable.get("webex_thread", {})

    room_id = webex_thread.get("room_id")
    parent_id = webex_thread.get("parent_id")
    if not room_id:
        return {
            "success": False,
            "error": "Missing webex_thread.room_id in config",
        }

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    success = asyncio.run(post_webex_message(room_id, message, parent_id=parent_id))
    return {"success": success}
