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


#: A reply that means "nothing to tell the channel" (Content Studio's convention).
QUIET_REPLY = "[quiet]"
#: Message field (``additional_kwargs`` key) deepagents uses for a task notification.
NOTIFICATION_KEY = "deepagents_notification"
#: Message field MDA uses the same way, for a notification of its own such as a
#: browser handoff ending.
MDA_NOTIFICATION_KEY = "mda_notification"


def is_task_notification(message: object) -> bool:
    """Whether a message is a deepagents or MDA notification rather than the user's."""
    extra = _field(message, "additional_kwargs")
    return any(
        (isinstance(message, Mapping) and key in message)
        or (isinstance(extra, Mapping) and key in extra)
        for key in (NOTIFICATION_KEY, MDA_NOTIFICATION_KEY)
    )


def extract_reply_text(run: Mapping[str, object] | object, *, after_id: str | None = None) -> str:
    """Return what a run says to the channel: each of its answers, oldest first.

    A run answers more than once when deepagents gives it another turn for task
    news that arrived after its answer; ``[quiet]`` answers are left out. The
    run starts after the message ``after_id`` (the last one before its input),
    or else at the last user message. With an ``after_id`` and no answer since
    it, the reply is empty.
    """
    answers = _answers(run, after_id)
    if _run_start(_messages(run), after_id) is None and len(answers) <= 1:
        return extract_assistant_text(run)
    # A known run start never falls back to an earlier run's answer.
    texts = (_message_text(answer) for answer in answers)
    return "\n\n".join(text for text in texts if text and text != QUIET_REPLY)


def extract_reply_content(
    run: Mapping[str, object], *, after_id: str | None = None
) -> MessageContent | None:
    """Content version of ``extract_reply_text``; several answers are joined as text."""
    answers = _answers(run, after_id)
    if len(answers) > 1:
        return extract_reply_text(run, after_id=after_id) or None
    if _run_start(_messages(run), after_id) is None:
        return extract_assistant_content(run)
    if not answers or _message_text(answers[0]) == QUIET_REPLY:
        return None
    return _message_content(answers[0])


def _messages(run: object) -> list[object]:
    messages = run.get("messages") if isinstance(run, Mapping) else None
    return messages if isinstance(messages, list) else []


def _answers(run: object, after_id: str | None) -> list[object]:
    """The run's answers: assistant messages with content (text or media) and no tool calls."""
    messages = _messages(run)
    start = _run_start(messages, after_id)
    if start is None:
        start = _last_user_message(messages)
    return [
        message
        for message in messages[start:]
        if _is_ai_message(message)
        and not _field(message, "tool_calls")
        and (_message_text(message) or _has_media(message))
    ]


#: Content block types that make a reply on their own, without text.
_MEDIA_BLOCKS = frozenset({"image", "image_url", "file", "audio", "video"})


def _has_media(message: object) -> bool:
    content = _field(message, "content")
    return isinstance(content, list) and any(
        isinstance(block, Mapping) and block.get("type") in _MEDIA_BLOCKS for block in content
    )


def _run_start(messages: list[object], after_id: str | None) -> int | None:
    """Index of the run's first message, just after ``after_id``; ``None`` when it isn't there."""
    if after_id is None:
        return None
    for index in range(len(messages) - 1, -1, -1):
        if _field(messages[index], "id") == after_id:
            return index + 1
    return None


def _last_user_message(messages: list[object]) -> int:
    """Index of the last user message; past the end when there is none."""
    for index in range(len(messages) - 1, -1, -1):
        if _is_user_message(messages[index]):
            return index
    return len(messages)


def _is_user_message(message: object) -> bool:
    """A message from the user; task notifications are user-role but come from deepagents."""
    is_user = _field(message, "type") in {"human", "user"} or _field(message, "role") == "user"
    return is_user and not is_task_notification(message)


def _field(message: object, name: str) -> object:
    return message.get(name) if isinstance(message, Mapping) else getattr(message, name, None)


__all__ = [
    "MDA_NOTIFICATION_KEY",
    "NOTIFICATION_KEY",
    "QUIET_REPLY",
    "extract_assistant_text",
    "extract_reply_content",
    "extract_reply_text",
    "is_task_notification",
]


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
