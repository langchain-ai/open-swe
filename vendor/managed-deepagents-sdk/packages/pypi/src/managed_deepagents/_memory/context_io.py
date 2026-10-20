"""Context Hub / filesystem IO helpers used by memory seeding and mounts."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from managed_deepagents._memory.async_util import await_if_needed

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

    from managed_deepagents._memory.types import MutablePathResult


def download_context_file(context_backend: BackendProtocol, path: str) -> str | None:
    download = getattr(context_backend, "download_files", None)
    if not callable(download):
        return None
    responses = await_if_needed(download([path]))
    if not isinstance(responses, (list, tuple)) or not responses:
        return None
    response = responses[0]
    if result_value(response, "error"):
        return None
    content = result_value(response, "content")
    if content is None:
        return None
    return as_text(content)


def as_text(content: object) -> str:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8")
    return str(content)


def result_value(result: object, key: str) -> object | None:
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)


def set_result_path(result: object, remap: Callable[[str], str]) -> None:
    if isinstance(result, dict):
        path = result.get("path")
        if isinstance(path, str):
            cast("dict[str, object]", result)["path"] = remap(path)
        return
    path = getattr(result, "path", None)
    if isinstance(path, str):
        cast("MutablePathResult", result).path = remap(path)
