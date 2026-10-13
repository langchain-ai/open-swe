"""Reading LangChain messages as transcript events see them.

Shared by the middleware, which records a run as it happens, and by
:mod:`openswe.transcript.backfill`, which folds a thread's finished history
into a log after the fact, so both describe the same message the same way.
"""

import base64
import binascii
import logging
import uuid
from collections.abc import Mapping
from typing import cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import JsonValue
from pydantic_core import PydanticSerializationError, to_jsonable_python

from openswe.transcript.attachments import PendingAttachment, UnsupportedAttachment
from openswe.transcript.events import (
    JsonObject,
    MessageAttachment,
    MessageUsage,
    ThreadCreated,
)

logger = logging.getLogger(__name__)

TOOL_OUTPUT_CAP_BYTES = 256 * 1024


def reasoning_text(message: BaseMessage) -> str:
    """Concatenated reasoning. Never stripped: a streaming chunk's leading space
    is part of the delta."""
    try:
        blocks = message.content_blocks
    except Exception:
        logger.debug("Could not read content blocks for reasoning", exc_info=True)
        return ""
    out = ""
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "reasoning":
            reasoning = block.get("reasoning")
            if isinstance(reasoning, str):
                out += reasoning
        elif block.get("type") == "non_standard":
            value = block.get("value")
            if isinstance(value, Mapping) and value.get("type") == "thinking":
                thinking = value.get("thinking")
                if isinstance(thinking, str):
                    out += thinking
    return out


def message_text(message: BaseMessage) -> str:
    try:
        return message.text
    except Exception:
        logger.debug("Could not read message text", exc_info=True)
        return ""


def transcribed_human_text(message: HumanMessage) -> str:
    """A human message as the reader has to receive it: envelope and all.

    Who sent it, on which surface, and whether it is a platform-generated
    context message the UI hides are all carried by the ``<input-message>``
    wrapper and by nothing else on the wire. Unwrapping here would strip a
    message of its attribution.
    """
    return message_text(message).strip()


def is_dynamic_context(message: HumanMessage) -> bool:
    """A ``<dynamic-context>`` introduction: who a sender is, not what they said."""
    return "<dynamic-context" in message_text(message)


def message_usage(message: AIMessage) -> MessageUsage | None:
    """Token accounting for one AI message, when the provider reported any."""
    usage = message.usage_metadata
    if not isinstance(usage, Mapping):
        return None
    counts = {
        key: value
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(value := usage.get(key), int)
    }
    metadata = message.response_metadata
    return (
        MessageUsage(
            **counts,
            model=nonempty_string(metadata.get("model_name")),
            invocation_id=nonempty_string(metadata.get("open_swe_invocation_id")),
        )
        if counts
        else None
    )


def _image_bytes(block: Mapping[str, object]) -> tuple[str, bytes] | None:
    """``(mime_type, data)`` for a standard base64 image content block."""
    mime_type = block.get("mime_type")
    encoded = block.get("base64")
    if not isinstance(mime_type, str) or not isinstance(encoded, str):
        return None
    try:
        return mime_type, base64.b64decode(encoded, validate=True)
    except binascii.Error:
        logger.warning("Skipping an undecodable transcript attachment", exc_info=True)
        return None


def human_attachments(
    message: HumanMessage, message_id: str
) -> tuple[list[MessageAttachment], tuple[PendingAttachment, ...]]:
    """Files on a human message, as event metadata plus the bytes to store.

    Only standard base64 image blocks are captured; a remote-URL image is
    referenced rather than copied, and anything else is skipped with a log.
    """
    try:
        blocks = message.content_blocks
    except Exception:
        logger.debug("Could not read content blocks for attachments", exc_info=True)
        return [], ()
    attachments: list[MessageAttachment] = []
    pending: list[PendingAttachment] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != "image":
            continue
        url = block.get("url")
        decoded = _image_bytes(block)
        if decoded is None:
            if isinstance(url, str) and url:
                attachments.append(MessageAttachment(mime_type="image/*", url=url))
            else:
                logger.warning(
                    "Skipping a transcript attachment with no bytes and no url",
                    extra={"transcript": {"message_id": message_id}},
                )
            continue
        mime_type, data = decoded
        file_name = block.get("file_name")
        attachment_id = uuid.uuid7()
        try:
            pending.append(
                PendingAttachment(
                    attachment_id=attachment_id,
                    message_id=message_id,
                    position=len(attachments),
                    mime_type=mime_type,
                    file_name=file_name if isinstance(file_name, str) else None,
                    data=data,
                )
            )
        except UnsupportedAttachment:
            logger.warning(
                "Skipping an unsupported transcript attachment",
                exc_info=True,
                extra={"transcript": {"message_id": message_id, "mime_type": mime_type}},
            )
            continue
        attachments.append(
            MessageAttachment(
                mime_type=mime_type,
                file_name=file_name if isinstance(file_name, str) else None,
                attachment_id=attachment_id,
            )
        )
    return attachments, tuple(pending)


def capped_tool_output(content: object) -> tuple[str, bool]:
    if isinstance(content, str):
        text = content
    elif isinstance(content, (list, dict)):
        parts: list[str] = []
        for block in content if isinstance(content, list) else [content]:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(cast(str, block["text"]))
            else:
                parts.append(str(block))
        text = "\n".join(parts)
    else:
        text = str(content)
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= TOOL_OUTPUT_CAP_BYTES:
        return text, False
    return encoded[:TOOL_OUTPUT_CAP_BYTES].decode("utf-8", "ignore"), True


def nonempty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def thread_created(metadata: Mapping[str, object], title: str) -> ThreadCreated:
    return ThreadCreated(
        type="thread.created",
        title=nonempty_string(metadata.get("title")) or title or "New agent",
        source=nonempty_string(metadata.get("source"))
        or nonempty_string(metadata.get("origin"))
        or "unknown",
        owner_login=nonempty_string(metadata.get("owner_login")) or "",
        visibility="private" if metadata.get("visibility") == "private" else "public",
        repo_owner=nonempty_string(metadata.get("repo_owner")),
        repo_name=nonempty_string(metadata.get("repo_name")),
        model_id=nonempty_string(metadata.get("resolved_model"))
        or nonempty_string(metadata.get("model")),
        effort=nonempty_string(metadata.get("resolved_effort"))
        or nonempty_string(metadata.get("effort")),
        metadata=json_object(metadata),
    )


def json_object(value: object) -> JsonObject:
    """``value`` as a JSON object, dropping keys pydantic could not encode."""
    if not isinstance(value, Mapping):
        return {} if value is None else {"value": repr(value)}
    out: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        try:
            out[key] = cast(JsonValue, to_jsonable_python(item))
        except PydanticSerializationError:
            logger.debug("Dropping unserializable transcript payload key", exc_info=True)
    return out
