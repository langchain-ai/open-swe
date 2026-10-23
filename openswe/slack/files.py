import logging
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from openswe.slack import client as slack_utils

logger = logging.getLogger(__name__)

_SLACK_FILE_DIR = "/workspace/.open-swe/slack-files"
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


def _sanitize_slack_filename(name: str, url: str) -> str:
    filename = name.strip()
    if not filename or "/" in filename or "\x00" in filename:
        parsed = urlparse(url).path
        filename = posixpath.basename(parsed) if parsed else ""
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._") or "slack-file"
    return filename[:120]


async def download_slack_files_to_sandbox(
    entries: list[SlackFileEntry], thread_id: str, *, workspace_slug: str | None = None
) -> list[StagedSlackFile]:
    """Best-effort download and staging of Slack files in the thread's sandbox."""
    if not entries:
        return []
    try:
        from openswe.sandboxes.lifecycle import ensure_sandbox_for_thread

        backend = await ensure_sandbox_for_thread(thread_id, workspace_slug=workspace_slug)
    except Exception:
        logger.warning(
            "Could not reach sandbox; skipping Slack file attachments",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
        return []
    staged: list[StagedSlackFile] = []
    used_names: set[str] = set()
    for entry in entries:
        try:
            content = await slack_utils.download_slack_file(entry.url)
            filename = _sanitize_slack_filename(entry.name, entry.url)
            base = filename
            suffix = 1
            while filename in used_names:
                filename = f"{base.rsplit('.', 1)[0] if '.' in base else base}-{suffix}"
                suffix += 1
            used_names.add(filename)
            sandbox_path = posixpath.join(_SLACK_FILE_DIR, filename)
            try:
                responses = await backend.aupload_files([(sandbox_path, content)])
            finally:
                del content
            if not responses:
                continue
            response = responses[0]
            error = (
                response.get("error")
                if isinstance(response, dict)
                else getattr(response, "error", None)
            )
            if error:
                logger.info(
                    "Slack file staging failed", extra={"slack_error": str(error), "file": filename}
                )
                continue
        except slack_utils.SlackFileDownloadError as error:
            logger.info("Slack file download skipped", extra={"slack_error": error.code})
            continue
        except Exception:
            logger.warning(
                "Slack file staging failed",
                extra={"agent_thread_id": thread_id, "file": entry.name},
                exc_info=True,
            )
            continue
        staged.append(StagedSlackFile(filename, sandbox_path))
    return staged


def slack_files_section(staged: list[StagedSlackFile]) -> str:
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
