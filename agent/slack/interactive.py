"""Registering the agent's own Block Kit elements so their clicks come back.

The agent writes ordinary Block Kit and picks its own ``action_id`` values. It
never sees a continuation token: every interactive element is registered here
and its ``action_id`` is rewritten to the token, so the click Slack delivers
names a row that says which thread to resume. The agent's original id travels
in that row and is what it is told about on the way back, so the name it chose
is the name it reads.

A ``url`` button and a section's ``image`` accessory are left untouched: Slack
handles the first itself and the second is not clickable. An ``input`` block is
refused, because a text input is only submitted through a modal.
"""

from typing import Any
from uuid import UUID

from agent.slack.continuations import SlackContinuation, action_id_for
from agent.utils.json_types import JsonObject

# An element that offers one of several answers stops being clickable once one
# of them is taken. One that names a value can be changed as often as the
# person likes.
_MULTI_USE_ELEMENTS = frozenset(
    {
        "channels_select",
        "checkboxes",
        "conversations_select",
        "datepicker",
        "datetimepicker",
        "external_select",
        "multi_channels_select",
        "multi_conversations_select",
        "multi_external_select",
        "multi_static_select",
        "multi_users_select",
        "number_input",
        "plain_text_input",
        "radio_buttons",
        "rich_text_input",
        "static_select",
        "timepicker",
        "users_select",
    }
)
_NON_INTERACTIVE_ELEMENTS = frozenset({"image"})


def _element_label(element: dict[str, Any]) -> str:
    for key in ("text", "placeholder"):
        holder = element.get(key)
        if isinstance(holder, dict) and isinstance(holder.get("text"), str):
            return holder["text"].strip()
    action_id = element.get("action_id")
    return action_id.strip() if isinstance(action_id, str) else ""


def _is_link_button(element: dict[str, Any]) -> bool:
    url = element.get("url")
    return element.get("type") == "button" and isinstance(url, str) and bool(url.strip())


def _needs_continuation(element: dict[str, Any]) -> bool:
    if element.get("type") in _NON_INTERACTIVE_ELEMENTS:
        return False
    return not _is_link_button(element)


def has_interactive_element(blocks: list[dict[str, Any]]) -> bool:
    """Whether any element in `blocks` would need a continuation to answer."""
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "input":
            return True
        elements = block.get("elements") if block.get("type") == "actions" else []
        accessory = block.get("accessory")
        candidates = [*(elements if isinstance(elements, list) else []), accessory]
        if any(
            isinstance(element, dict) and _needs_continuation(element) for element in candidates
        ):
            return True
    return False


class UnsupportedBlocks(Exception):
    """Blocks holding an element whose interaction could never be answered."""


def prepare(
    blocks: list[dict[str, Any]],
    *,
    thread_id: str,
    channel_id: str,
    thread_ts: str,
    run_config: JsonObject,
) -> tuple[list[dict[str, Any]], list[SlackContinuation]]:
    """``blocks`` with interactive elements registered, and the rows to store.

    Raises :class:`UnsupportedBlocks` for an element no interaction can reach.
    """
    prepared: list[dict[str, Any]] = []
    rows: list[SlackContinuation] = []

    def register(element: dict[str, Any]) -> dict[str, Any]:
        if not _needs_continuation(element):
            return element
        row = SlackContinuation(
            thread_id=thread_id,
            action_id=str(element.get("action_id") or "").strip() or "unnamed",
            element_type=str(element.get("type") or "element"),
            label=_element_label(element),
            channel_id=channel_id,
            thread_ts=thread_ts,
            run_config=run_config,
            single_use=element.get("type") not in _MULTI_USE_ELEMENTS,
        )
        rows.append(row)
        return {**element, "action_id": action_id_for(row.id)}

    for block in blocks:
        if not isinstance(block, dict):
            raise UnsupportedBlocks("every block must be an object")
        if block.get("type") == "input":
            raise UnsupportedBlocks(
                "an `input` block is only submitted through a modal, which a reply cannot "
                "open. Ask for the value in the message text instead"
            )
        updated = dict(block)
        if block.get("type") == "actions" and isinstance(block.get("elements"), list):
            updated["elements"] = [
                register(element) if isinstance(element, dict) else element
                for element in block["elements"]
            ]
        accessory = block.get("accessory")
        if isinstance(accessory, dict):
            updated["accessory"] = register(accessory)
        prepared.append(updated)

    return prepared, rows


def tokens(rows: list[SlackContinuation]) -> list[UUID]:
    return [row.id for row in rows]
