"""Slack files a thread's sandbox receives once its run has opened it.

A message can bring files before the thread has a sandbox: Managed Deep Agents
creates the box only when the thread's run starts. The webhook records where each
file goes and where Slack serves it; the run's first connection to its box, before
the model runs, downloads the files and writes them there.
"""

import logging
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from langgraph_sdk import get_client
from pydantic import BaseModel, TypeAdapter, ValidationError

from openswe.slack import client as slack_utils

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

PENDING_SLACK_FILES_KEY = "slack_files_pending"
SLACK_FILE_DIR = "/workspace/.open-swe/slack-files"
_MAX_SLACK_FILE_ATTACHMENTS = 10


@dataclass(frozen=True)
class SlackFileEntry:
    """A non-image file Slack attached to a message."""

    name: str
    url: str


@dataclass(frozen=True)
class StagedSlackFile:
    """A Slack file staged in the thread's sandbox."""

    filename: str
    sandbox_path: str


def slack_file_entries(messages: Sequence[Mapping[str, object]]) -> list[SlackFileEntry]:
    """Non-image files attached to the given messages, in first-seen order."""
    entries: list[SlackFileEntry] = []
    seen: set[str] = set()
    for message in messages:
        files = message.get("files")
        if not isinstance(files, list):
            continue
        for file_info in files:
            if not isinstance(file_info, dict) or file_info.get("url_private") in (None, ""):
                continue
            mimetype = file_info.get("mimetype")
            if isinstance(mimetype, str) and mimetype.startswith("image/"):
                continue
            url = str(file_info["url_private"])
            if url in seen:
                continue
            seen.add(url)
            name = file_info.get("name")
            entries.append(SlackFileEntry(name=name if isinstance(name, str) else "", url=url))
            if len(entries) >= _MAX_SLACK_FILE_ATTACHMENTS:
                return entries
    return entries


def slack_files_section(staged: list[StagedSlackFile]) -> str:
    """The prompt section listing files staged for the thread's sandbox, or empty."""
    if not staged:
        return ""
    lines = [
        "## Slack File Attachments",
        "These files from Slack were staged into this thread's sandbox:",
        *[
            f"- `{file.sandbox_path}` (originally uploaded to Slack as `{file.filename}`)"
            for file in staged
        ],
    ]
    return "\n".join(lines)


class _PendingFile(BaseModel):
    url: str
    path: str


_pending_files = TypeAdapter(list[_PendingFile])


def _pending(metadata: Mapping[str, object]) -> list[_PendingFile]:
    try:
        return _pending_files.validate_python(metadata.get(PENDING_SLACK_FILES_KEY) or [])
    except ValidationError:
        logger.warning("Dropping unreadable pending Slack files", exc_info=True)
        return []


def _sanitize_slack_filename(name: str, url: str) -> str:
    filename = name.strip()
    if not filename or "/" in filename or "\x00" in filename:
        parsed = urlparse(url).path
        filename = posixpath.basename(parsed) if parsed else ""
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._") or "slack-file"
    return filename[:120]


async def queue_slack_files(thread_id: str, entries: list[SlackFileEntry]) -> list[StagedSlackFile]:
    """Where each file goes in the thread's sandbox, recorded for the run to write it there."""
    if not entries:
        return []
    # deferred: the sandbox registry pulls in deepagents, which the webapp never loads
    from openswe.sandboxes.state import get_sandbox_metadata

    pending = _pending(await get_sandbox_metadata(thread_id))
    used_names = {posixpath.basename(file.path) for file in pending}
    staged: list[StagedSlackFile] = []
    for entry in entries:
        filename = _sanitize_slack_filename(entry.name, entry.url)
        base = filename
        suffix = 1
        while filename in used_names:
            filename = f"{base.rsplit('.', 1)[0] if '.' in base else base}-{suffix}"
            suffix += 1
        used_names.add(filename)
        sandbox_path = posixpath.join(SLACK_FILE_DIR, filename)
        pending.append(_PendingFile(url=entry.url, path=sandbox_path))
        staged.append(StagedSlackFile(filename, sandbox_path))
    await get_client().threads.update(
        thread_id=thread_id,
        metadata={PENDING_SLACK_FILES_KEY: _pending_files.dump_python(pending, mode="json")},
    )
    return staged


async def deliver_slack_files(
    thread_id: str, metadata: Mapping[str, object], backend: SandboxBackendProtocol
) -> None:
    """Download the files waiting for the thread's sandbox and write them there, once.

    Best-effort, as staging always was: a failed download or write only skips that file.
    Files a message queued after ``metadata`` was read stay queued for the next run.
    """
    pending = _pending(metadata)
    if not pending:
        return
    from openswe.sandboxes.state import get_sandbox_metadata

    delivered = {(file.url, file.path) for file in pending}
    left = [
        file
        for file in _pending(await get_sandbox_metadata(thread_id))
        if (file.url, file.path) not in delivered
    ]
    await get_client().threads.update(
        thread_id=thread_id,
        metadata={
            PENDING_SLACK_FILES_KEY: _pending_files.dump_python(left, mode="json") if left else None
        },
    )
    for file in pending:
        try:
            content = await slack_utils.download_slack_file(file.url)
        except slack_utils.SlackFileDownloadError as error:
            logger.info("Slack file download skipped", extra={"slack_error": error.code})
            continue
        try:
            responses = await backend.aupload_files([(file.path, content)])
        finally:
            del content
        error = responses[0].error if responses else "no response"
        if error:
            logger.info(
                "Slack file staging failed", extra={"slack_error": str(error), "file": file.path}
            )
