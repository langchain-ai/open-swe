"""A Codex rollout (``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``) as LangChain messages."""

import json
import logging
import re
import uuid
from typing import Annotated, Literal

from langchain_core.messages.tool import ToolCall
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    JsonValue,
    Tag,
    TypeAdapter,
    ValidationError,
)

from openswe.threads.session_messages import LocalSession, SessionMessages, TranscriptError

logger = logging.getLogger(__name__)

# Context Codex sends as a user message chunk, such as the environment or AGENTS.md; nobody typed it.
_HARNESS_TAGS = (
    "environment_context|user_instructions|user_shell_command|turn_aborted|permissions instructions"
    "|apps_instructions|collaboration_mode|skill|subagent_notification"
)
_HARNESS_CONTEXT = re.compile(
    rf"^\s*(<({_HARNESS_TAGS})\b[^>]*>.*</\2>|# AGENTS\.md instructions\b.*)\s*$", re.DOTALL
)
_IMAGE_PLACEHOLDER = "[image omitted]"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _ContentPart(_Model):
    type: str
    text: str | None = None

    @property
    def shown(self) -> str:
        if self.type == "input_image":
            return _IMAGE_PLACEHOLDER
        if self.text is None or _HARNESS_CONTEXT.match(self.text):
            return ""
        return self.text.strip()


class _Message(_Model):
    type: Literal["message"]
    role: str
    content: list[_ContentPart]

    @property
    def text(self) -> str:
        return "\n\n".join(shown for part in self.content if (shown := part.shown))


class _FunctionCall(_Model):
    type: Literal["function_call"]
    name: str
    call_id: str
    arguments: str = ""

    @property
    def call(self) -> ToolCall:
        try:
            args = json.loads(self.arguments) if self.arguments else {}
        except json.JSONDecodeError:
            args = self.arguments
        return ToolCall(
            id=self.call_id,
            name=self.name,
            args=args if isinstance(args, dict) else {"input": args},
            type="tool_call",
        )


class _CustomToolCall(_Model):
    type: Literal["custom_tool_call"]
    name: str
    call_id: str
    input: str = ""

    @property
    def call(self) -> ToolCall:
        return ToolCall(
            id=self.call_id, name=self.name, args={"input": self.input}, type="tool_call"
        )


class _ToolOutput(_Model):
    type: Literal["function_call_output", "custom_tool_call_output"]
    call_id: str
    output: JsonValue = None

    @property
    def text(self) -> str:
        output = self.output
        if isinstance(output, dict) and isinstance(output.get("content"), str | list):
            output = output["content"]
        if isinstance(output, list):
            return "\n\n".join(
                str(part["text"]) for part in output if isinstance(part, dict) and "text" in part
            )
        return output if isinstance(output, str) else json.dumps(output)


class _OtherItem(_Model):
    """Reasoning, web searches and items newer Codex releases add, which do not carry over."""

    type: str

    @staticmethod
    def tag(value: object) -> str:
        """The union member an item parses as."""
        kind = value.get("type") if isinstance(value, dict) else None
        if not isinstance(kind, str) or kind not in _ITEM_TYPES:
            return "other"
        return "output" if kind.endswith("_output") else kind


_ITEM_TYPES = frozenset(
    {
        "message",
        "function_call",
        "custom_tool_call",
        "function_call_output",
        "custom_tool_call_output",
    }
)


type _Item = Annotated[
    Annotated[_Message, Tag("message")]
    | Annotated[_FunctionCall, Tag("function_call")]
    | Annotated[_CustomToolCall, Tag("custom_tool_call")]
    | Annotated[_ToolOutput, Tag("output")]
    | Annotated[_OtherItem, Tag("other")],
    Discriminator(_OtherItem.tag),
]


class _SessionMeta(_Model):
    id: str


class _MetaLine(_Model):
    type: Literal["session_meta"]
    payload: _SessionMeta


class _ItemLine(_Model):
    type: Literal["response_item"]
    payload: _Item


class _OtherLine(_Model):
    type: str

    @staticmethod
    def tag(value: object) -> str:
        """The union member a line parses as."""
        kind = value.get("type") if isinstance(value, dict) else None
        return (
            kind if isinstance(kind, str) and kind in {"session_meta", "response_item"} else "other"
        )


type _Line = Annotated[
    Annotated[_MetaLine, Tag("session_meta")]
    | Annotated[_ItemLine, Tag("response_item")]
    | Annotated[_OtherLine, Tag("other")],
    Discriminator(_OtherLine.tag),
]

_LINE: TypeAdapter[_Line] = TypeAdapter(_Line)


class CodexTranscript:
    """A rollout's JSONL taken one line at a time, folded straight into messages."""

    def __init__(self) -> None:
        self._builder = SessionMessages()
        self._session_id: str | None = None
        self._reply_id: str | None = None
        self._lines = 0
        self._parsed = 0

    @classmethod
    def parse(cls, transcript: str) -> LocalSession:
        reader = cls()
        for line in transcript.splitlines():
            reader.add(line)
        return reader.session()

    def add(self, line: str) -> None:
        self._lines += 1
        if not line.strip():
            return
        try:
            parsed = _LINE.validate_json(line)
        except ValidationError:
            logger.warning(
                "Skipped a Codex rollout line that did not parse",
                extra={"transcript_line": self._lines},
                exc_info=True,
            )
            return
        self._parsed += 1
        match parsed:
            case _MetaLine(payload=meta):
                self._session_id = self._session_id or meta.id
            case _ItemLine(payload=item):
                self._fold(item)
            case _:
                pass

    def session(self) -> LocalSession:
        """The folded messages; raises ``TranscriptError`` when no line was a rollout record."""
        if self._lines and not self._parsed:
            raise TranscriptError("no line is a Codex rollout record")
        return LocalSession(
            title=self._builder.title(lambda text: text.strip().split("\n", 1)[0]),
            messages=self._builder.finish(),
            session_id=self._session_id,
        )

    def _fold(self, item: _Item) -> None:
        match item:
            case _Message(role="user"):
                self._reply_id = None
                self._builder.human(item.text, None)
            case _Message(role="assistant"):
                self._builder.assistant(self._reply(), item.text, [])
            case _FunctionCall() | _CustomToolCall():
                self._builder.assistant(self._reply(), "", [item.call])
            case _ToolOutput():
                self._reply_id = None
                self._builder.tool_result(item.call_id, item.text)
            case _:
                pass

    def _reply(self) -> str:
        """The id the reply under way is built under, so its text and calls form one message."""
        self._reply_id = self._reply_id or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"codex-reply:{self._session_id}:{self._lines}")
        )
        return self._reply_id
