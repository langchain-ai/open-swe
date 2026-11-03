"""Credential-free MCP results, and the browser sessions they carried.

The model sees an MCP result with its credentials redacted. A browser session in it
is held under an opaque handle, out of the thread's history, traces and sandbox,
until ``browser_run`` installs it.
"""

import json
import re
from contextvars import ContextVar
from typing import Final
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

type Sessions = dict[str, dict[str, JsonValue]]

# The sessions a backend operation's MCP calls held back, for the run to keep.
HELD_SESSIONS: ContextVar[Sessions | None] = ContextVar("held_mcp_sessions", default=None)
_TOKEN_KEYS: Final = frozenset({"access_token", "refresh_token"})
_MIN_SECRET: Final = 16
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
_TOKEN = re.compile(r"""(?i)["']?(?:access|refresh)[_-]token["']?\s*[:=]\s*["']?([^\s,"'}]+)""")
_json = TypeAdapter(JsonValue)


class McpResult:
    """One response, with credentials collected before all copies are removed."""

    def __init__(self, content: object, artifact: object) -> None:
        self.content = content
        self.artifact = artifact
        self.original = _json.validate_python({"content": content, "artifact": artifact})
        self.secrets: set[str] = set()
        self._collect(self.original)

    @staticmethod
    def _container(value: str) -> JsonValue:
        try:
            parsed = json.loads(value)
        except ValueError, RecursionError:
            return None
        return parsed if isinstance(parsed, dict | list) else None

    @classmethod
    def scrub(cls, text: str) -> str:
        """``text`` with any credential it carries redacted."""
        redacted = cls(text, None).redacted(text)
        return redacted if isinstance(redacted, str) else text

    def _collect(self, value: JsonValue, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                self._collect(item, name)
        elif isinstance(value, list):
            for item in value:
                self._collect(item)
        elif isinstance(value, str):
            if key.lower().replace("-", "_") in _TOKEN_KEYS and len(value) >= _MIN_SECRET:
                self.secrets.add(value)
            self.secrets.update(_JWT.findall(value))
            if (parsed := self._container(value)) is not None:
                self._collect(parsed)
                return
            self.secrets.update(m for m in _TOKEN.findall(value) if len(m) >= _MIN_SECRET)

    def redacted(self, value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            return {
                str(self.redacted(k)): "[REDACTED]"
                if k.lower().replace("-", "_") in _TOKEN_KEYS and isinstance(v, str)
                else self.redacted(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.redacted(item) for item in value]
        if not isinstance(value, str) or not self.secrets:
            return value
        if (parsed := self._container(value)) is not None:
            cleaned = self.redacted(parsed)
            return value if cleaned == parsed else json.dumps(cleaned, ensure_ascii=False)
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value

    def withheld(self, held: Sessions | None) -> tuple[object, object]:
        """The response for the model; a browser session in it goes to ``held`` under a handle."""
        if not self.secrets or not isinstance(self.original, dict):
            return self.content, self.artifact
        content = self.redacted(self.original["content"])
        artifact = self.redacted(self.original["artifact"])
        session = self._session(self.original)
        if held is None or session is None:
            return content, artifact
        handle = uuid4().hex
        held[handle] = session
        note: JsonValue = {"type": "text", "text": json.dumps({"session_handle": handle})}
        return [*content, note] if isinstance(content, list) else [content, note], artifact

    def _session(self, value: JsonValue) -> dict[str, JsonValue] | None:
        if isinstance(value, str):
            value = self._container(value)
        if isinstance(value, dict):
            if all(key in value for key in ("host", "storage_key", "session")):
                return value
            value = list(value.values())
        if isinstance(value, list):
            for item in value:
                if found := self._session(item):
                    return found
        return None
