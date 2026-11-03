"""Credential-free MCP results and private sandbox handoffs."""

import json
import re
from collections.abc import Sequence
from typing import Final, Protocol

from pydantic import JsonValue, TypeAdapter

SECRETS_MARKER: Final = "open-swe-secrets-"
SESSION_FILE: Final = re.compile(rf"/tmp/{SECRETS_MARKER}[A-Za-z0-9]+/response\.json")
_TOKEN_KEYS: Final = frozenset({"access_token", "refresh_token"})
_MIN_SECRET: Final = 16
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
_TOKEN = re.compile(r"""(?i)["']?(?:access|refresh)[_-]token["']?\s*[:=]\s*["']?([^\s,"'}]+)""")
_json = TypeAdapter(JsonValue)


class Execution(Protocol):
    @property
    def output(self) -> str: ...

    @property
    def exit_code(self) -> int | None: ...


class Upload(Protocol):
    @property
    def error(self) -> str | None: ...


class SecretSandbox(Protocol):
    async def aexecute(self, command: str) -> Execution: ...

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Sequence[Upload]: ...


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

    async def protect(self, sandbox: SecretSandbox | None) -> tuple[object, object]:
        """The response for the model; credentials go to a private sandbox file it names."""
        if not self.secrets or not isinstance(self.original, dict):
            return self.content, self.artifact
        if sandbox is None:
            raise ValueError("Cannot retain MCP credentials without a sandbox")
        made = await sandbox.aexecute(
            f"umask 077; d=$(mktemp -d /tmp/{SECRETS_MARKER}XXXXXXXXXXXX) && "
            'touch "$d/response.json" && printf %s "$d/response.json"'
        )
        path = made.output.strip()
        if made.exit_code != 0 or not SESSION_FILE.fullmatch(path):
            raise ValueError("Cannot create private MCP response file")
        uploaded = await sandbox.aupload_files([(path, json.dumps(self.original).encode())])
        if len(uploaded) != 1 or uploaded[0].error:
            raise ValueError("Cannot save private MCP response file")
        content = self.redacted(self.original["content"])
        note: JsonValue = {"type": "text", "text": json.dumps({"session_file": path})}
        blocks = [*content, note] if isinstance(content, list) else [content, note]
        return blocks, self.redacted(self.original["artifact"])

    def session(self) -> dict[str, JsonValue]:
        found = self._session(self.original)
        if found is None:
            raise ValueError("No browser session in MCP response")
        return found

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
