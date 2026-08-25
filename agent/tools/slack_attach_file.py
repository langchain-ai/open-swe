import posixpath
import shlex
import uuid
from typing import Any

from langgraph.config import get_config

from ..utils.slack import (
    get_active_slack_thread,
    slack_thread_mutation_lock,
    upload_slack_thread_file,
)
from ..utils.thread_ops import langgraph_client
from .create_sandbox_file_download_url import _resolve_sandbox_file

_MAX_SLACK_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MAX_COMMENT_CHARS = 3000


async def slack_attach_file(
    file_path: str,
    title: str | None = None,
    initial_comment: str | None = None,
) -> dict[str, Any]:
    """Attach a sandbox file to the current Slack thread.

    Use this when the user asks to receive or preview a generated file directly in Slack. The file
    must be a regular file inside the active sandbox work directory and no larger than 10 MB. Slack
    may render supported formats, including HTML in enabled workspaces, inline. Do not attach secrets,
    credentials, private keys, environment files, or other sensitive data.
    """
    backend, path, work_dir = await _resolve_sandbox_file(file_path)
    staged_path = posixpath.join(work_dir, f".open-swe-slack-upload-{uuid.uuid4().hex}")
    prepare = await backend.aexecute(_prepare_file_command(path, staged_path))
    if prepare.exit_code != 0:
        await _remove_staged_file(backend, staged_path)
        return {"success": False, "error": _prepare_error(prepare.output)}
    try:
        size = int(prepare.output.strip())
    except (AttributeError, ValueError):
        await _remove_staged_file(backend, staged_path)
        return {"success": False, "error": "failed to determine file size"}
    if size < 1:
        await _remove_staged_file(backend, staged_path)
        return {"success": False, "error": "file cannot be empty"}
    if size > _MAX_SLACK_ATTACHMENT_BYTES:
        await _remove_staged_file(backend, staged_path)
        return {"success": False, "error": "file exceeds the 10 MB attachment limit"}

    try:
        filename = posixpath.basename(path)
        if len(filename) > 255:
            return {"success": False, "error": "filename exceeds 255 characters"}
        if _has_control_chars(filename):
            return {"success": False, "error": "filename contains control characters"}

        comment = initial_comment.strip() if isinstance(initial_comment, str) else None
        if comment and len(comment) > _MAX_COMMENT_CHARS:
            return {"success": False, "error": "initial_comment exceeds 3000 characters"}
        if comment and _has_control_chars(comment, allowed="\n\t"):
            return {"success": False, "error": "initial_comment contains control characters"}
        display_title = title.strip() if isinstance(title, str) and title.strip() else None
        if display_title and len(display_title) > 255:
            return {"success": False, "error": "title exceeds 255 characters"}
        if display_title and _has_control_chars(display_title):
            return {"success": False, "error": "title contains control characters"}

        config = get_config()
        configurable = config.get("configurable", {})
        slack_thread = configurable.get("slack_thread", {})
        thread_id = configurable.get("thread_id")
        client = langgraph_client()
        active = await get_active_slack_thread(
            client,
            thread_id if isinstance(thread_id, str) else None,
            slack_thread if isinstance(slack_thread, dict) else None,
        )
        active = active or {}
        channel_id = active.get("channel_id")
        thread_ts = active.get("thread_ts")
        if not channel_id or not thread_ts:
            return {"success": False, "error": "Missing active Slack thread in config"}

        downloads = await backend.adownload_files([staged_path])
        content = _download_content(downloads[0] if downloads else None)
        if content is None or len(content) != size:
            return {"success": False, "error": "failed to read staged sandbox file"}

        async with slack_thread_mutation_lock(client, channel_id, thread_ts):
            current = await get_active_slack_thread(
                client,
                thread_id if isinstance(thread_id, str) else None,
            )
            if not current or (current.get("channel_id"), current.get("thread_ts")) != (
                channel_id,
                thread_ts,
            ):
                return {"success": False, "error": "Slack thread moved; retry the attachment"}

            file_id, error = await upload_slack_thread_file(
                channel_id,
                thread_ts,
                filename,
                content,
                title=display_title,
                initial_comment=comment,
            )
        if error:
            return {"success": False, "error": error}
        return {"success": True, "file_id": file_id, "filename": filename}
    finally:
        await _remove_staged_file(backend, staged_path)


def _prepare_file_command(path: str, staged_path: str) -> str:
    source = shlex.quote(path)
    staged = shlex.quote(staged_path)
    maximum = _MAX_SLACK_ATTACHMENT_BYTES
    return (
        f"set -eu; [ -f {source} ] || exit 2; size=$(stat -Lc %s -- {source}) || exit 2; "
        f'[ "$size" -gt 0 ] || {{ printf empty; exit 3; }}; '
        f'[ "$size" -le {maximum} ] || {{ printf large; exit 4; }}; '
        f"cp --reflink=auto -- {source} {staged}; "
        f'[ "$(stat -Lc %s -- {staged})" -eq "$size" ] || exit 5; printf \'%s\' "$size"'
    )


def _prepare_error(output: str) -> str:
    normalized = output.strip() if isinstance(output, str) else ""
    if normalized == "empty":
        return "file cannot be empty"
    if normalized == "large":
        return "file exceeds the 10 MB attachment limit"
    return (
        "file_path must identify a regular file"
        if not normalized
        else "failed to stage sandbox file"
    )


async def _remove_staged_file(backend: Any, path: str) -> None:
    await backend.aexecute(f"rm -f -- {shlex.quote(path)}")


def _has_control_chars(value: str, *, allowed: str = "") -> bool:
    return any(ord(char) < 32 and char not in allowed for char in value)


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
