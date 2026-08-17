"""Signed links and bounded reads for sandbox file downloads."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import posixpath
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import jwt

from .dashboard_links import dashboard_base_url

SANDBOX_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024
_SANDBOX_DOWNLOAD_TIMEOUT_SECONDS = 120
_SANDBOX_CLEANUP_TIMEOUT_SECONDS = 10
_SANDBOX_DOWNLOAD_AUDIENCE = "open-swe-sandbox-file"
_SANDBOX_DOWNLOAD_TOKEN_TYPE = "sandbox-file-download"
_JWT_ALGORITHM = "HS256"


class SandboxDownloadError(ValueError):
    pass


class SandboxDownloadFileNotFound(SandboxDownloadError):
    pass


class SandboxDownloadFileTooLarge(SandboxDownloadError):
    pass


class InvalidSandboxDownloadToken(SandboxDownloadError):
    pass


@dataclass(frozen=True)
class SandboxFileInfo:
    path: str
    filename: str
    size: int


@dataclass(frozen=True)
class SandboxDownloadLink:
    url: str


@dataclass(frozen=True)
class SandboxDownloadClaims:
    thread_id: str
    sandbox_id: str
    file_path: str


def normalize_sandbox_file_path(file_path: str) -> str:
    if not isinstance(file_path, str) or not file_path or "\x00" in file_path:
        raise SandboxDownloadError("file_path must be a non-empty absolute path")
    if len(file_path) > 4096 or not posixpath.isabs(file_path):
        raise SandboxDownloadError("file_path must be a non-empty absolute path")
    normalized = posixpath.normpath(file_path)
    if (
        normalized != file_path
        or normalized == "/"
        or posixpath.basename(normalized) in {"", ".", ".."}
    ):
        raise SandboxDownloadError("file_path must be a normalized absolute file path")
    return normalized


async def inspect_sandbox_file(backend: Any, file_path: str) -> SandboxFileInfo:
    path = normalize_sandbox_file_path(file_path)
    command_path = _backend_command_path(backend, path)
    result = await backend.aexecute(
        _inspect_command(command_path), timeout=_SANDBOX_DOWNLOAD_TIMEOUT_SECONDS
    )
    payload = _command_payload(result)
    if payload.get("ok") is not True:
        if payload.get("error") == "not_found":
            raise SandboxDownloadFileNotFound("sandbox file not found")
        raise SandboxDownloadError("sandbox file could not be inspected")
    size = payload.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise SandboxDownloadError("sandbox file size is unavailable")
    if size > SANDBOX_DOWNLOAD_MAX_BYTES:
        raise SandboxDownloadFileTooLarge("sandbox file exceeds the 100 MiB download limit")
    return SandboxFileInfo(path=path, filename=posixpath.basename(path), size=size)


async def download_sandbox_file(backend: Any, file_path: str) -> tuple[SandboxFileInfo, bytes]:
    info = await inspect_sandbox_file(backend, file_path)
    staged_path, staged_size = await _stage_sandbox_file(backend, info.path)
    try:
        async with asyncio.timeout(_SANDBOX_DOWNLOAD_TIMEOUT_SECONDS):
            downloads = await backend.adownload_files([staged_path])
        if not downloads:
            raise SandboxDownloadFileNotFound("sandbox file could not be downloaded")
        error = _value(downloads[0], "error")
        if error:
            if "not_found" in str(error).lower() or "path_not_found" in str(error).lower():
                raise SandboxDownloadFileNotFound("sandbox file could not be downloaded")
            raise SandboxDownloadError("sandbox file could not be downloaded")
        content = _download_content(downloads[0])
        if content is None:
            raise SandboxDownloadError("sandbox file content is unavailable")
        if len(content) > SANDBOX_DOWNLOAD_MAX_BYTES:
            raise SandboxDownloadFileTooLarge("sandbox file exceeds the 100 MiB download limit")
        current_info = SandboxFileInfo(path=info.path, filename=info.filename, size=staged_size)
        return current_info, content
    finally:
        await _delete_staged_file(backend, staged_path)


async def _stage_sandbox_file(backend: Any, file_path: str) -> tuple[str, int]:
    staged_path = f"/tmp/open-swe-download-{uuid.uuid4().hex}"
    command_file_path = _backend_command_path(backend, file_path)
    command_staged_path = _backend_command_path(backend, staged_path)
    try:
        result = await backend.aexecute(
            _stage_command(command_file_path, command_staged_path, staged_path),
            timeout=_SANDBOX_DOWNLOAD_TIMEOUT_SECONDS,
        )
        payload = _command_payload(result)
        if payload.get("ok") is not True:
            error = payload.get("error")
            if error == "too_large":
                raise SandboxDownloadFileTooLarge("sandbox file exceeds the 100 MiB download limit")
            if error == "not_found":
                raise SandboxDownloadFileNotFound("sandbox file could not be staged")
            raise SandboxDownloadError("sandbox file could not be staged")
        returned_path = payload.get("path")
        size = payload.get("size")
        if (
            returned_path != staged_path
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise SandboxDownloadError("sandbox file staging returned an invalid response")
        if size > SANDBOX_DOWNLOAD_MAX_BYTES:
            raise SandboxDownloadFileTooLarge("sandbox file exceeds the 100 MiB download limit")
        return staged_path, size
    except Exception:
        await _delete_staged_file(backend, staged_path)
        raise


async def _delete_staged_file(backend: Any, staged_path: str) -> None:
    try:
        async with asyncio.timeout(_SANDBOX_CLEANUP_TIMEOUT_SECONDS):
            await backend.adelete(staged_path)
    except Exception:
        pass


def _backend_command_path(backend: Any, file_path: str) -> str:
    try:
        current = getattr(backend, "current", None)
    except Exception:
        current = None
    command_backend = current if current is not None else backend
    if getattr(command_backend, "virtual_mode", False) is True:
        return file_path.removeprefix("/")
    return file_path


def _inspect_command(file_path: str) -> str:
    encoded = _encoded_payload({"path": file_path})
    script = r"""python - <<'PY'
import base64
import json
from pathlib import Path

payload = json.loads(base64.b64decode('__PAYLOAD__').decode())
path = Path(payload['path'])
try:
    if not path.is_file():
        raise FileNotFoundError
    print(json.dumps({'ok': True, 'size': path.stat().st_size}))
except FileNotFoundError:
    print(json.dumps({'ok': False, 'error': 'not_found'}))
except OSError:
    print(json.dumps({'ok': False, 'error': 'io_error'}))
PY"""
    return script.replace("__PAYLOAD__", encoded)


def _stage_command(file_path: str, staged_path: str, returned_path: str) -> str:
    encoded = _encoded_payload(
        {
            "path": file_path,
            "staged_path": staged_path,
            "returned_path": returned_path,
            "max_bytes": SANDBOX_DOWNLOAD_MAX_BYTES,
        }
    )
    script = r"""python - <<'PY'
import base64
import json
from pathlib import Path

payload = json.loads(base64.b64decode('__PAYLOAD__').decode())
source = Path(payload['path'])
destination = Path(payload['staged_path'])
returned_path = payload['returned_path']
limit = payload['max_bytes']
total = 0
try:
    if not source.is_file():
        raise FileNotFoundError
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as input_file, destination.open('xb') as output_file:
        while chunk := input_file.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise OverflowError
            output_file.write(chunk)
    print(json.dumps({'ok': True, 'path': returned_path, 'size': total}))
except OverflowError:
    destination.unlink(missing_ok=True)
    print(json.dumps({'ok': False, 'error': 'too_large'}))
except FileNotFoundError:
    destination.unlink(missing_ok=True)
    print(json.dumps({'ok': False, 'error': 'not_found'}))
except OSError:
    destination.unlink(missing_ok=True)
    print(json.dumps({'ok': False, 'error': 'io_error'}))
PY"""
    return script.replace("__PAYLOAD__", encoded)


def _encoded_payload(payload: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _command_payload(result: Any) -> dict[str, Any]:
    output = _value(result, "output")
    exit_code = _value(result, "exit_code")
    if not isinstance(output, str) or exit_code not in {0, None}:
        raise SandboxDownloadError("sandbox file command failed")
    try:
        payload = json.loads(output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SandboxDownloadError("sandbox file command returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise SandboxDownloadError("sandbox file command returned an invalid response")
    return payload


def create_sandbox_download_link(
    thread_id: str,
    sandbox_id: str,
    file_path: str,
) -> SandboxDownloadLink:
    path = normalize_sandbox_file_path(file_path)
    if not isinstance(thread_id, str) or not thread_id:
        raise SandboxDownloadError("thread_id is required")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise SandboxDownloadError("sandbox_id is required")
    token = jwt.encode(
        {
            "aud": _SANDBOX_DOWNLOAD_AUDIENCE,
            "typ": _SANDBOX_DOWNLOAD_TOKEN_TYPE,
            "tid": thread_id,
            "sid": sandbox_id,
            "path": path,
        },
        _jwt_secret(),
        algorithm=_JWT_ALGORITHM,
    )
    url = f"{dashboard_base_url()}/dashboard/api/sandbox-files/{quote(token, safe='')}"
    return SandboxDownloadLink(url=url)


def decode_sandbox_download_token(token: str) -> SandboxDownloadClaims:
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[_JWT_ALGORITHM],
            audience=_SANDBOX_DOWNLOAD_AUDIENCE,
            options={"require": ["aud", "typ", "tid", "sid", "path"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidSandboxDownloadToken("invalid sandbox download URL") from exc
    if payload.get("typ") != _SANDBOX_DOWNLOAD_TOKEN_TYPE:
        raise InvalidSandboxDownloadToken("invalid sandbox download URL")
    thread_id = payload.get("tid")
    sandbox_id = payload.get("sid")
    file_path = payload.get("path")
    if (
        not isinstance(thread_id, str)
        or not thread_id
        or not isinstance(sandbox_id, str)
        or not sandbox_id
        or not isinstance(file_path, str)
    ):
        raise InvalidSandboxDownloadToken("invalid sandbox download URL")
    try:
        normalized_path = normalize_sandbox_file_path(file_path)
    except SandboxDownloadError as exc:
        raise InvalidSandboxDownloadToken("invalid sandbox download URL") from exc
    return SandboxDownloadClaims(
        thread_id=thread_id,
        sandbox_id=sandbox_id,
        file_path=normalized_path,
    )


def _jwt_secret() -> str:
    secret = os.environ.get("DASHBOARD_JWT_SECRET", "")
    if not secret:
        raise RuntimeError("DASHBOARD_JWT_SECRET not configured")
    return secret


def _download_content(result: Any) -> bytes | None:
    for key in ("content", "data", "bytes"):
        value = _value(result, key)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
    file_data = _value(result, "file_data")
    if isinstance(file_data, bytes):
        return file_data
    if isinstance(file_data, str):
        return file_data.encode()
    if isinstance(file_data, Mapping):
        for key in ("content", "data", "bytes"):
            value = file_data.get(key)
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode()
    return None


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)
