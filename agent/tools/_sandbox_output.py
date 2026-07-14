from __future__ import annotations

import json
import posixpath
import uuid
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from ..utils.sandbox_paths import aresolve_sandbox_work_dir
from ..utils.sandbox_state import get_sandbox_backend

OUTPUT_CHUNK_CHARS = 500


def chunk_output_as_jsonl(content: str) -> str:
    records = (
        {"chunk": index, "text": content[offset : offset + OUTPUT_CHUNK_CHARS]}
        for index, offset in enumerate(range(0, max(len(content), 1), OUTPUT_CHUNK_CHARS), start=1)
    )
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records)


async def write_sandbox_output(tool_name: str, content: str, extension: str) -> str:
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not thread_id:
        raise RuntimeError("no thread_id in run config")

    backend = await get_sandbox_backend(str(thread_id))
    work_dir = await aresolve_sandbox_work_dir(backend)
    suffix = extension.removeprefix(".")
    path = posixpath.join(work_dir, f"{tool_name}-{uuid.uuid4().hex}.{suffix}")
    result = await backend.awrite(path, content)
    error = _value(result, "error")
    if error:
        raise RuntimeError(str(error))
    return path


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)
