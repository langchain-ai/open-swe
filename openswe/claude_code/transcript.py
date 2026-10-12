"""A Claude Code session transcript (``~/.claude/projects/<project>/<session>.jsonl``) as LangChain messages."""

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    TypeAdapter,
    ValidationError,
    ValidatorFunctionWrapHandler,
    WrapValidator,
)

logger = logging.getLogger(__name__)

_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.DOTALL)
IMAGE_PLACEHOLDER = "[image omitted]"
MISSING_TOOL_RESULT = "No result was recorded before the session was uploaded."
MAX_TITLE_CHARS = 80


class TranscriptError(ValueError):
    """The transcript is not a Claude Code session."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TextBlock(_Model):
    type: Literal["text"]
    text: str


class ImageBlock(_Model):
    type: Literal["image"]


class OtherBlock(_Model):
    """Thinking, server-tool and document blocks, which do not carry over."""

    type: str


def _tag(known: frozenset[str]) -> Callable[[object], str]:
    def tag(value: object) -> str:
        kind = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
        return kind if isinstance(kind, str) and kind in known else "other"

    return tag


def _or_other(value: object, handler: ValidatorFunctionWrapHandler) -> object:
    """Claude Code reshapes blocks between releases; one that no longer parses is skipped, not fatal."""
    try:
        return handler(value)
    except ValidationError:
        kind = value.get("type") if isinstance(value, dict) else None
        logger.warning(
            "Skipped a Claude Code content block that did not parse",
            extra={"block_type": kind},
            exc_info=True,
        )
        return OtherBlock(type=kind if isinstance(kind, str) else "unknown")


type ResultPart = Annotated[
    Annotated[TextBlock, Tag("text")]
    | Annotated[ImageBlock, Tag("image")]
    | Annotated[OtherBlock, Tag("other")],
    Discriminator(_tag(frozenset({"text", "image"}))),
    WrapValidator(_or_other),
]


class ToolUseBlock(_Model):
    type: Literal["tool_use"]
    id: str
    name: str
    input: JsonValue = None

    @property
    def args(self) -> dict[str, JsonValue]:
        return self.input if isinstance(self.input, dict) else {"input": self.input}


class ToolResultBlock(_Model):
    type: Literal["tool_result"]
    tool_use_id: str
    content: str | list[ResultPart] | None = None
    is_error: bool | None = None


type ContentBlock = Annotated[
    Annotated[TextBlock, Tag("text")]
    | Annotated[ImageBlock, Tag("image")]
    | Annotated[ToolUseBlock, Tag("tool_use")]
    | Annotated[ToolResultBlock, Tag("tool_result")]
    | Annotated[OtherBlock, Tag("other")],
    Discriminator(_tag(frozenset({"text", "image", "tool_use", "tool_result"}))),
    WrapValidator(_or_other),
]


class _Record(_Model):
    uuid: str
    parent_uuid: str | None = Field(default=None, alias="parentUuid")
    is_sidechain: bool = Field(default=False, alias="isSidechain")


class UserMessage(_Model):
    content: str | list[ContentBlock]


class UserRecord(_Record):
    type: Literal["user"]
    message: UserMessage
    is_meta: bool = Field(default=False, alias="isMeta")
    is_compact_summary: bool = Field(default=False, alias="isCompactSummary")


class AssistantMessage(_Model):
    id: str
    content: list[ContentBlock]


class AssistantRecord(_Record):
    type: Literal["assistant"]
    message: AssistantMessage


class QueuedPrompt(_Model):
    """A message the person sent while a turn was running."""

    type: Literal["queued_command"]
    prompt: str | list[ContentBlock]
    command_mode: str = Field(default="prompt", alias="commandMode")


class OtherAttachment(_Model):
    type: str


class AttachmentRecord(_Record):
    type: Literal["attachment"]
    attachment: Annotated[
        Annotated[QueuedPrompt, Tag("queued_command")] | Annotated[OtherAttachment, Tag("other")],
        Discriminator(_tag(frozenset({"queued_command"}))),
    ]


class CustomTitleRecord(_Model):
    type: Literal["custom-title"]
    custom_title: str = Field(alias="customTitle")


class OtherRecord(_Model):
    type: str


class LinkRecord(_Record):
    """A record in the parent chain that carries no message, such as a ``system`` stop-hook summary."""

    type: str


_MESSAGE_RECORD_TYPES = frozenset({"user", "assistant", "attachment", "custom-title"})


def _record_tag(value: object) -> str:
    if not isinstance(value, dict):
        return "other"
    kind = value.get("type")
    if isinstance(kind, str) and kind in _MESSAGE_RECORD_TYPES:
        return kind
    return "link" if isinstance(value.get("uuid"), str) else "other"


type Record = Annotated[
    Annotated[UserRecord, Tag("user")]
    | Annotated[AssistantRecord, Tag("assistant")]
    | Annotated[AttachmentRecord, Tag("attachment")]
    | Annotated[CustomTitleRecord, Tag("custom-title")]
    | Annotated[LinkRecord, Tag("link")]
    | Annotated[OtherRecord, Tag("other")],
    Discriminator(_record_tag),
]

_RECORD: TypeAdapter[Record] = TypeAdapter(Record)

type ConversationRecord = UserRecord | AssistantRecord | AttachmentRecord | LinkRecord


@dataclass(frozen=True, kw_only=True)
class ClaudeSession:
    title: str | None
    messages: list[BaseMessage]


def _parse_record(number: int, line: str) -> Record | LinkRecord | None:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        logger.warning(
            "Skipped a Claude Code transcript line that is not JSON",
            extra={"transcript_line": number},
            exc_info=True,
        )
        return None
    try:
        return _RECORD.validate_python(raw)
    except ValidationError:
        logger.warning(
            "Claude Code transcript record did not parse",
            extra={"transcript_line": number},
            exc_info=True,
        )
    try:
        return LinkRecord.model_validate(raw)
    except ValidationError:
        return None


def _hangs_off_branch(record: ConversationRecord) -> bool:
    """Parallel tool results and their attachments parent to their own ``tool_use`` line, off the main path."""
    if isinstance(record, AttachmentRecord | LinkRecord):
        return True
    return (
        isinstance(record, UserRecord)
        and isinstance(record.message.content, list)
        and all(isinstance(block, ToolResultBlock) for block in record.message.content)
    )


def _active_branch(conversation: Sequence[ConversationRecord]) -> list[ConversationRecord]:
    """The newest message's ancestry plus what hangs off it, in file order, so rewound branches drop out."""
    if not conversation:
        return []
    by_uuid = {record.uuid: record for record in conversation}
    active: set[str] = set()
    node: ConversationRecord | None = conversation[-1]
    while node is not None and node.uuid not in active:
        active.add(node.uuid)
        node = by_uuid.get(node.parent_uuid) if node.parent_uuid else None
    for record in conversation:
        if record.parent_uuid in active and record.uuid not in active and _hangs_off_branch(record):
            active.add(record.uuid)
    return [record for record in conversation if record.uuid in active]


def _clean(text: str) -> str:
    return _SYSTEM_REMINDER.sub("", text).strip()


type Block = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock | OtherBlock


def _parts_text(parts: Sequence[Block]) -> str:
    texts: list[str] = []
    for part in parts:
        if isinstance(part, TextBlock):
            if cleaned := _clean(part.text):
                texts.append(cleaned)
        elif isinstance(part, ImageBlock):
            texts.append(IMAGE_PLACEHOLDER)
    return "\n\n".join(texts)


def _content_text(content: str | Sequence[Block] | None) -> str:
    if content is None:
        return ""
    return _clean(content) if isinstance(content, str) else _parts_text(content)


class _Builder:
    """Folds the branch into messages, keeping every tool call answered before the next human turn."""

    def __init__(self) -> None:
        self.messages: list[BaseMessage] = []
        self._ai: dict[str, AIMessage] = {}
        self._calls: dict[str, str] = {}
        self._pending: dict[str, None] = {}
        self._deferred: list[HumanMessage] = []

    def human(self, text: str, message_id: str | None) -> None:
        if not text:
            return
        message = HumanMessage(content=text, id=message_id)
        if self._pending:
            self._deferred.append(message)
        else:
            self.messages.append(message)

    def assistant(self, record: AssistantRecord) -> None:
        text = _parts_text(record.message.content)
        uses = [block for block in record.message.content if isinstance(block, ToolUseBlock)]
        calls = [
            ToolCall(id=use.id, name=use.name, args=use.args, type="tool_call") for use in uses
        ]
        existing = self._ai.get(record.message.id)
        if existing is None:
            if not text and not calls:
                return
            self._flush_deferred()
            message = AIMessage(content=text, tool_calls=calls, id=record.message.id)
            self._ai[record.message.id] = message
            self.messages.append(message)
        else:
            existing.content = "\n\n".join(part for part in (str(existing.content), text) if part)
            existing.tool_calls.extend(calls)
        for use in uses:
            self._calls[use.id] = use.name
            self._pending[use.id] = None

    def tool_result(self, block: ToolResultBlock) -> None:
        name = self._calls.get(block.tool_use_id)
        if name is None or block.tool_use_id not in self._pending:
            logger.warning(
                "Dropped a Claude Code tool result with no open tool call",
                extra={"tool_use_id": block.tool_use_id},
            )
            return
        del self._pending[block.tool_use_id]
        self.messages.append(
            ToolMessage(
                content=_content_text(block.content),
                tool_call_id=block.tool_use_id,
                name=name,
                status="error" if block.is_error else "success",
            )
        )
        if not self._pending:
            self._flush_deferred()

    def finish(self) -> list[BaseMessage]:
        self._flush_deferred()
        return self.messages

    def _flush_deferred(self) -> None:
        """Close calls that never got a result, then release the humans held behind them."""
        for tool_call_id in list(self._pending):
            self.messages.append(
                ToolMessage(
                    content=MISSING_TOOL_RESULT,
                    tool_call_id=tool_call_id,
                    name=self._calls[tool_call_id],
                )
            )
        self._pending.clear()
        self.messages.extend(self._deferred)
        self._deferred.clear()


class ClaudeTranscript:
    """A session's JSONL taken one line at a time, keeping only what the conversation needs."""

    def __init__(self) -> None:
        self._records: list[ConversationRecord] = []
        self._title: str | None = None
        self._line = 0
        self._parsed = 0

    @classmethod
    def parse(cls, transcript: str) -> ClaudeSession:
        reader = cls()
        for line in transcript.splitlines():
            reader.add(line)
        return reader.session()

    def add(self, line: str) -> None:
        self._line += 1
        if not line.strip():
            return
        record = _parse_record(self._line, line)
        if record is None:
            return
        self._parsed += 1
        if isinstance(record, CustomTitleRecord):
            self._title = record.custom_title.strip() or self._title
        elif not isinstance(record, OtherRecord) and not record.is_sidechain:
            self._records.append(record)

    def session(self) -> ClaudeSession:
        """Fold the lines into messages; raises ``TranscriptError`` when none was a record."""
        if self._line and not self._parsed:
            raise TranscriptError("no line is a Claude Code transcript record")
        messages = _messages(self._records)
        first_human = next((m for m in messages if isinstance(m, HumanMessage)), None)
        title = self._title
        if title is None and first_human is not None:
            title = str(first_human.content).strip()[:MAX_TITLE_CHARS] or None
        return ClaudeSession(title=title, messages=messages)


def _messages(records: Sequence[ConversationRecord]) -> list[BaseMessage]:
    builder = _Builder()
    for record in _active_branch(records):
        match record:
            case AssistantRecord():
                builder.assistant(record)
            case UserRecord(is_meta=True, is_compact_summary=False):
                continue
            case UserRecord(message=UserMessage(content=str() as content)):
                builder.human(_clean(content), record.uuid)
            case UserRecord(message=UserMessage(content=list() as blocks)):
                for block in blocks:
                    if isinstance(block, ToolResultBlock):
                        builder.tool_result(block)
                builder.human(
                    _parts_text(
                        [block for block in blocks if not isinstance(block, ToolResultBlock)]
                    ),
                    record.uuid,
                )
            case AttachmentRecord(attachment=QueuedPrompt(command_mode="prompt") as queued):
                builder.human(_content_text(queued.prompt), record.uuid)
            case _:
                continue
    return builder.finish()
