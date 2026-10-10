"""LangChain messages as a Claude Code session transcript that ``claude --resume`` continues."""

import json
import logging
import re
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import JsonValue

from openswe.input_messages import authored_text

logger = logging.getLogger(__name__)

_UNSAFE_PATH_CHARS = re.compile(r"[^a-zA-Z0-9]")
_UNSAFE_TOOL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_TOOL_NAME_CHARS = 128
_FALLBACK_MODEL = "open-swe"
MISSING_TOOL_RESULT = "No result was recorded in the Open SWE thread."

type Record = dict[str, JsonValue]


def _safe_tool_text(value: str | None, fallback: str) -> str:
    return _UNSAFE_TOOL_CHARS.sub("_", value or "")[:_MAX_TOOL_NAME_CHARS] or fallback


@dataclass(frozen=True, kw_only=True)
class ClaudeSessionFile:
    """A Claude Code session on a person's machine: its id, working directory and branch."""

    session_id: str
    cwd: str
    git_branch: str | None = None

    @property
    def path(self) -> str:
        """Where Claude Code keeps the transcript, as a shell word to double-quote."""
        project = _UNSAFE_PATH_CHARS.sub("-", self.cwd)
        return f"${{CLAUDE_CONFIG_DIR:-$HOME/.claude}}/projects/{project}/{self.session_id}.jsonl"

    def lines(
        self, messages: Sequence[BaseMessage], *, title: str | None, note: str | None = None
    ) -> Iterator[str]:
        """The transcript's JSONL lines; ``note`` is a closing message the person does not see."""
        writer = _Writer(self)
        for message in messages:
            match message:
                case HumanMessage():
                    writer.human(authored_text(message.content))
                case AIMessage():
                    writer.assistant(message)
                case ToolMessage():
                    writer.tool_result(message)
                case _:
                    continue
        writer.finish()
        if note:
            writer.user(note, meta=True)
        if title:
            writer.records.append(
                {"type": "custom-title", "customTitle": title, "sessionId": self.session_id}
            )
        for record in writer.records:
            yield json.dumps(record, ensure_ascii=False)


class _Writer:
    """Chains records parent to child, answering every tool call before the next turn starts."""

    def __init__(self, file: ClaudeSessionFile) -> None:
        self.records: list[Record] = []
        self._file = file
        self._parent: str | None = None
        self._clock = datetime.now(UTC)
        self._tool_use_ids: dict[str, str] = {}
        self._used_ids: set[str] = set()
        self._pending: dict[str, str] = {}
        self._deferred: list[str] = []

    def human(self, text: str | None) -> None:
        if not text:
            return
        if self._pending:
            self._deferred.append(text)
        else:
            self.user(text)

    def user(self, text: str, *, meta: bool = False) -> None:
        self._append(
            "user", {"role": "user", "content": text}, **({"isMeta": True} if meta else {})
        )

    def assistant(self, message: AIMessage) -> None:
        self.finish()
        content: list[JsonValue] = []
        if text := message.text.strip():
            content.append({"type": "text", "text": text})
        for call in message.tool_calls:
            tool_use_id = self._tool_use_id(call["id"])
            name = _safe_tool_text(call["name"], "tool")
            content.append(
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": call["args"]}
            )
            self._pending[tool_use_id] = name
        if not content:
            return
        model = message.response_metadata.get("model_name") or message.response_metadata.get(
            "model"
        )
        self._append(
            "assistant",
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "model": model if isinstance(model, str) and model else _FALLBACK_MODEL,
                "content": content,
                "stop_reason": "tool_use" if message.tool_calls else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        )

    def tool_result(self, message: ToolMessage) -> None:
        tool_use_id = self._tool_use_ids.get(message.tool_call_id)
        if tool_use_id is None or tool_use_id not in self._pending:
            logger.warning(
                "Dropped a tool result with no open tool call from a Claude Code export",
                extra={"tool_call_id": message.tool_call_id},
            )
            return
        del self._pending[tool_use_id]
        self._result(tool_use_id, message.text, is_error=message.status == "error")
        if not self._pending:
            self._release_deferred()

    def finish(self) -> None:
        """Close calls that never got a result, then release the humans held behind them."""
        for tool_use_id in list(self._pending):
            self._result(tool_use_id, MISSING_TOOL_RESULT, is_error=False)
        self._pending.clear()
        self._release_deferred()

    def _release_deferred(self) -> None:
        for text in self._deferred:
            self.user(text)
        self._deferred.clear()

    def _result(self, tool_use_id: str, text: str, *, is_error: bool) -> None:
        block: Record = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
        if is_error:
            block["is_error"] = True
        self._append("user", {"role": "user", "content": [block]})

    def _tool_use_id(self, tool_call_id: str | None) -> str:
        """A unique id Anthropic accepts; providers that reuse ids across turns map to the newest call."""
        base = _safe_tool_text(tool_call_id, "toolu")
        candidate, suffix = base, 1
        while candidate in self._used_ids:
            candidate, suffix = f"{base}_{suffix}", suffix + 1
        self._used_ids.add(candidate)
        if tool_call_id:
            self._tool_use_ids[tool_call_id] = candidate
        return candidate

    def _append(self, kind: str, message: Record, **extra: JsonValue) -> None:
        record_uuid = str(uuid.uuid4())
        self._clock += timedelta(milliseconds=1)
        self.records.append(
            {
                "parentUuid": self._parent,
                "isSidechain": False,
                "userType": "external",
                "cwd": self._file.cwd,
                "sessionId": self._file.session_id,
                "gitBranch": self._file.git_branch,
                "type": kind,
                "message": message,
                "uuid": record_uuid,
                "timestamp": self._clock.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                **extra,
            }
        )
        self._parent = record_uuid
