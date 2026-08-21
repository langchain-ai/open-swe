"""Getting a stuck thread's uncommitted work out of its sandbox, as a patch.

The work only exists in the sandbox's worktree, so the patch has to be built
there. The program that does it is :mod:`agent.resources.recovery_patch`, a real
module shipped into the sandbox on stdin; this module runs it, checks what it
reports, and downloads the file it wrote.
"""

import base64
import json
import logging
from collections.abc import Mapping
from importlib import resources
from typing import Any

from fastapi import HTTPException

from .turn_checkpoint import response_ok, response_output

logger = logging.getLogger(__name__)

PATCH_LIMIT_BYTES = 25 * 1024 * 1024
PATCH_TIMEOUT_SECONDS = 120


def recovery_patch_filename(thread_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in thread_id)
    return f"open-swe-{(safe or 'thread')[:80]}.patch"


def _repo_name(metadata: Mapping[str, Any]) -> str:
    name = metadata.get("repo_name")
    if not isinstance(name, str) or not name:
        repo = metadata.get("repo")
        name = repo.get("name") if isinstance(repo, dict) else None
    return name if isinstance(name, str) else ""


def _patch_command(metadata: Mapping[str, Any], thread_id: str) -> str:
    base_branch = metadata.get("base_branch")
    payload = {
        "repo_name": _repo_name(metadata),
        "base_branch": base_branch if isinstance(base_branch, str) else "main",
        "thread_key": recovery_patch_filename(thread_id).removesuffix(".patch"),
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    program = (
        resources.files("agent.resources").joinpath("recovery_patch.py").read_text(encoding="utf-8")
    )
    # base64 keeps the payload to characters no shell will reinterpret.
    return f"python - '{encoded}' <<'OPEN_SWE_RECOVERY_PATCH'\n{program}\nOPEN_SWE_RECOVERY_PATCH"


def _download_content(result: Any) -> bytes | None:
    for attr in ("content", "data", "bytes"):
        value = result.get(attr) if isinstance(result, dict) else getattr(result, attr, None)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
    file_data = (
        result.get("file_data") if isinstance(result, dict) else getattr(result, "file_data", None)
    )
    if isinstance(file_data, bytes):
        return file_data
    if isinstance(file_data, str):
        return file_data.encode()
    if isinstance(file_data, dict):
        for key in ("content", "data", "bytes"):
            value = file_data.get(key)
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode()
    return None


async def generate_recovery_patch(
    sandbox: Any, metadata: Mapping[str, Any], thread_id: str
) -> tuple[bytes, str]:
    """``(patch bytes, download filename)`` for a thread's uncommitted work."""
    try:
        result = await sandbox.aexecute(
            _patch_command(metadata, thread_id),
            timeout=PATCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch generation failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    output = response_output(result).strip()
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        logger.debug("Invalid recovery patch response for %s: %s", thread_id, output)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    if not response_ok(result) or payload.get("ok") is not True:
        detail = payload.get("error") if isinstance(payload.get("error"), str) else None
        logger.debug("Recovery patch generation failed for %s: %s", thread_id, detail)
        raise HTTPException(502, detail or "failed to generate recovery patch")

    size = payload.get("size")
    if not isinstance(size, int):
        raise HTTPException(502, "failed to generate recovery patch")
    if size == 0:
        raise HTTPException(404, "thread has no recoverable changes")
    if size > PATCH_LIMIT_BYTES:
        raise HTTPException(413, "recovery patch is too large to download")

    patch_path = payload.get("path")
    if not isinstance(patch_path, str) or not patch_path.startswith("/tmp/"):
        raise HTTPException(502, "failed to generate recovery patch")

    try:
        downloads = await sandbox.adownload_files([patch_path])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch download failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to download recovery patch") from exc
    if not downloads:
        raise HTTPException(502, "failed to download recovery patch")
    content = _download_content(downloads[0])
    if content is None:
        raise HTTPException(502, "failed to download recovery patch")
    return content, recovery_patch_filename(thread_id)
