"""Extract the latest usable assistant output from a run or agent state."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from managed_deepagents._channels.types import MessageContent


def extract_assistant_text(run: Mapping[str, object] | object) -> str:
    """Return the last non-empty assistant message text, or ``\"\"``."""
    for message in _assistant_messages(run):
        text = _message_text(message)
        if text:
            return text
    return ""


def _assistant_messages(run: object) -> Iterator[object]:
    """Yield assistant messages newest first, including those from earlier turns."""
    if not isinstance(run, Mapping):
        return
    messages = run.get("messages")
    if not isinstance(messages, list):
        return
    for message in reversed(messages):
        if _is_ai_message(message):
            yield message


def _is_ai_message(message: object) -> bool:
    msg_type = getattr(message, "type", None)
    if msg_type is None and isinstance(message, Mapping):
        msg_type = message.get("type") or message.get("role")
    return msg_type in {"ai", "assistant"}


def _message_text(message: object) -> str:
    # LangChain BaseMessage exposes ``.text``; serialized dicts use ``content``.
    text_attr = getattr(message, "text", None)
    if isinstance(text_attr, str) and text_attr.strip():
        return text_attr.strip()
    content = getattr(message, "content", None)
    if content is None and isinstance(message, Mapping):
        content = message.get("content")
    return _content_as_text(content).strip()


def _content_as_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text")
                if block.get("type") == "text" and isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


#: Message field (``additional_kwargs`` key) MDA marks its own notifications with, such
#: as a browser handoff ending, as deepagents marks a task notification.
MDA_NOTIFICATION_KEY = "mda_notification"


__all__ = ["MDA_NOTIFICATION_KEY", "extract_assistant_text"]


def extract_assistant_content(run: Mapping[str, object]) -> MessageContent | None:
    for message in _assistant_messages(run):
        content = _message_content(message)
        if content is not None:
            return content
    return None


def _message_content(message: object) -> MessageContent | None:
    content = (
        message.get("content")
        if isinstance(message, Mapping)
        else getattr(message, "content", None)
    )
    if isinstance(content, str):
        return content if content.strip() else None
    if not isinstance(content, list) or not content:
        return None
    blocks: list[dict[str, object]] = []
    for block in content:
        if isinstance(block, str):
            blocks.append({"type": "text", "text": block})
        elif isinstance(block, dict):
            blocks.append(block)
        else:
            return None
    return cast("MessageContent", blocks)
