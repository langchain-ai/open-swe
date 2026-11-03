"""Credential-free MCP results, and the browser sessions they carried.

The model sees an MCP result with its credentials redacted. A browser session in it
is replaced, whole, by an opaque handle, and held out of the thread's history, traces
and sandbox until ``browser_sign_in`` installs it.
"""

import json
import re
from typing import Final
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

type Sessions = dict[str, dict[str, JsonValue]]

# Compared with case, "_" and "-" removed, so `refreshToken` is `refresh_token`.
_TOKEN_KEYS: Final = frozenset(
    {"accesstoken", "refreshtoken", "idtoken", "providertoken", "providerrefreshtoken"}
)
_SESSION_KEYS: Final = ("host", "storage_key", "session")
_MIN_SECRET: Final = 12
_REDACTED: Final = "[REDACTED]"
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
# `access_token=...` in prose. A value followed by "(" or without a digit reads as code
# (`access_token = self._get_access_token()`), not as a credential.
_TOKEN = re.compile(
    r"""(?i)["']?(?:access|refresh|id|provider(?:[_-]?refresh)?)[_-]?token["']?\s*[:=]\s*["']?"""
    r"""([A-Za-z0-9._~+/=-]++)(?!\()"""
)
_json = TypeAdapter(JsonValue)


class McpResult:
    """One response, with credentials collected before all copies are removed."""

    def __init__(self, content: object, artifact: object) -> None:
        self.content = content
        self.artifact = artifact
        self.original = _json.validate_python({"content": content, "artifact": artifact})
        self.secrets: set[str] = set()
        self._collect(self.original)
        self.session = self._session(self.original)
        # The handle the model sees in place of ``session``, once it is held.
        self._handle: str | None = None

    @staticmethod
    def _container(value: str) -> JsonValue:
        try:
            parsed = json.loads(value)
        except ValueError, RecursionError:
            return None
        return parsed if isinstance(parsed, dict | list) else None

    @staticmethod
    def _token_key(key: str) -> bool:
        return key.lower().replace("_", "").replace("-", "") in _TOKEN_KEYS

    @staticmethod
    def _session_shaped(value: JsonValue) -> bool:
        return (
            isinstance(value, dict)
            and isinstance(value.get("session"), dict)
            and all(isinstance(value.get(key), str) for key in _SESSION_KEYS[:2])
        )

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
            if self._token_key(key) and len(value) >= _MIN_SECRET:
                self.secrets.add(value)
            self.secrets.update(_JWT.findall(value))
            if (parsed := self._container(value)) is not None:
                self._collect(parsed)
                return
            self.secrets.update(
                found
                for found in _TOKEN.findall(value)
                if len(found) >= _MIN_SECRET and any(c.isdigit() for c in found)
            )

    def redacted(self, value: JsonValue) -> JsonValue:
        if self._session_shaped(value):
            if self._handle is not None and value == self.session:
                return {"session_handle": self._handle}
            return _REDACTED
        if isinstance(value, dict):
            return {
                str(self.redacted(k)): _REDACTED
                if self._token_key(k) and isinstance(v, str)
                else self.redacted(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.redacted(item) for item in value]
        if not isinstance(value, str):
            return value
        if (parsed := self._container(value)) is not None:
            cleaned = self.redacted(parsed)
            return value if cleaned == parsed else json.dumps(cleaned, ensure_ascii=False)
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, _REDACTED)
        return value

    def withheld(self, held: Sessions | None) -> tuple[object, object]:
        """The response for the model; a browser session in it goes to ``held`` under a handle."""
        if not (self.secrets or self.session) or not isinstance(self.original, dict):
            return self.content, self.artifact
        if held is not None and self.session is not None:
            self._handle = uuid4().hex
            held[self._handle] = self.session
        content = self.redacted(self.original["content"])
        artifact = self.redacted(self.original["artifact"])
        if self._handle is None or self._handle in json.dumps(content):
            return content, artifact
        # The session was only in the artifact, which the model never reads.
        note: JsonValue = {"type": "text", "text": json.dumps({"session_handle": self._handle})}
        return [*content, note] if isinstance(content, list) else [content, note], artifact

    def _session(self, value: JsonValue) -> dict[str, JsonValue] | None:
        if isinstance(value, str):
            value = self._container(value)
        if isinstance(value, dict):
            if self._session_shaped(value):
                return value
            value = list(value.values())
        if isinstance(value, list):
            for item in value:
                if found := self._session(item):
                    return found
        return None
