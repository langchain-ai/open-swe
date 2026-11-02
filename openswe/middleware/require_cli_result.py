"""Keep a bridged thread's run from ending without the result its CLI prints."""

import logging

from openswe.input_messages import (
    SystemIdentity,
    build_input_messages,
)
from openswe.middleware.message_content import content_to_text
from openswe.prompts import prompt

logger = logging.getLogger(__name__)

CLI_RESULT_GUARD: SystemIdentity = {
    "id": "system:cli-result-guard",
    "display_name": "CLI result guard",
    "platform": "open-swe",
}


def cli_result_nudge(tool_name: str, introduced: set[str] | None = None) -> list[str]:
    """What asks the model again for the CLI result it owes, introducing the guard unless ``introduced``."""
    instruction = prompt("runs/missing-cli-result", result_tool=tool_name)
    built = build_input_messages(
        instruction,
        {"sender_id": CLI_RESULT_GUARD["id"], "surface": "automation", "kind": "system"},
        systems=[CLI_RESULT_GUARD],
        injected_dynamic_context_hashes=set(introduced or ()),
    )
    return [content_to_text(message["content"]) for message in built]
