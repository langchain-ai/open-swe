"""Credential-free MCP results and private sandbox handoffs."""

import json
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import JsonValue, TypeAdapter

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
_TOKEN = re.compile(r"""(?i)(["']?(?:access|refresh)[_-]token["']?\s*[:=]\s*["']?)([^\s,"'}]+)""")
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
        self.original = _json.validate_python({"content": content, "artifact": artifact})
        self.secrets: set[str] = set()
        self._collect(self.original)

    def _collect(self, value: JsonValue, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                self._collect(item, name)
        elif isinstance(value, list):
            for item in value:
                self._collect(item)
        elif isinstance(value, str):
            if key.lower().replace("-", "_") in {"access_token", "refresh_token"} and value:
                self.secrets.add(value)
            self.secrets.update(_JWT.findall(value))
            self.secrets.update(match[1] for match in _TOKEN.findall(value))
            try:
                parsed = json.loads(value)
            except ValueError, RecursionError:
                return
            if isinstance(parsed, dict | list):
                self._collect(parsed)

    def redacted(self, value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            return {
                str(self.redacted(k)): "[REDACTED]"
                if k.lower().replace("-", "_") in {"access_token", "refresh_token"}
                else self.redacted(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.redacted(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError, RecursionError:
                parsed = None
            if isinstance(parsed, dict | list):
                return json.dumps(self.redacted(parsed))
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            return value
        return value

    async def protect(self, sandbox: SecretSandbox | None) -> tuple[JsonValue, JsonValue]:
        if not isinstance(self.original, dict):
            raise TypeError("Invalid MCP response")
        content = self.redacted(self.original["content"])
        artifact = self.redacted(self.original["artifact"])
        if not self.secrets:
            return content, artifact
        if sandbox is None:
            raise ValueError("Cannot retain MCP credentials without a sandbox")
        made = await sandbox.aexecute(
            "umask 077; d=$(mktemp -d /tmp/open-swe-secrets-XXXXXXXXXXXX) && "
            'touch "$d/response.json" && printf %s "$d/response.json"'
        )
        path = made.output.strip()
        if made.exit_code != 0 or not re.fullmatch(
            r"/tmp/open-swe-secrets-[A-Za-z0-9]+/response\.json", path
        ):
            raise ValueError("Cannot create private MCP response file")
        uploaded = await sandbox.aupload_files([(path, json.dumps(self.original).encode())])
        if len(uploaded) != 1 or uploaded[0].error:
            raise ValueError("Cannot save private MCP response file")
        note: JsonValue = {"type": "text", "text": json.dumps({"session_file": path})}
        return [*content, note] if isinstance(content, list) else [content, note], artifact

    def session(self) -> dict[str, JsonValue]:
        found = self._session(self.original)
        if found is None:
            raise ValueError("No browser session in MCP response")
        return found

    def _session(self, value: JsonValue) -> dict[str, JsonValue] | None:
        if isinstance(value, dict):
            if all(key in value for key in ("host", "storage_key", "session")):
                return value
            for item in value.values():
                if found := self._session(item):
                    return found
        elif isinstance(value, list):
            for item in value:
                if found := self._session(item):
                    return found
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError, RecursionError:
                return None
            if isinstance(parsed, dict | list):
                return self._session(parsed)
        return None
