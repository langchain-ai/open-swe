"""Tool: ``request_pr_media_upload``. Prepare a human-approved PR media upload."""

import logging
import shlex
from typing import Any

from langchain_core.tools import tool

from agent.github import pr_media, pr_media_support
from agent.prompts import load_prompt
from agent.run_config import RunConfig
from agent.tools.create_sandbox_file_download_url import resolve_sandbox_file
from agent.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)


async def _request_pr_media_upload(
    file_path: str,
    pull_number: int,
) -> dict[str, Any]:
    """Implement the `request_pr_media_upload` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "no thread_id in run config"}
    login = cfg.github_login
    if not isinstance(login, str) or not login.strip():
        return {
            "success": False,
            "error": "media uploads require a user-owned thread with a GitHub login",
        }
    login = login.strip()
    repo = cfg.repo
    if repo is None or not repo.owner or not repo.name:
        return {"success": False, "error": "no repository configured for this thread"}
    if not pr_media.valid_repo_part(repo.owner) or not pr_media.valid_repo_part(repo.name):
        return {"success": False, "error": "configured repository name is invalid"}
    if not isinstance(pull_number, int) or pull_number < 1:
        return {"success": False, "error": "pull_number must be a positive integer"}

    try:
        backend, source_path, _ = await resolve_sandbox_file(file_path)
        file_name = source_path.rsplit("/", 1)[-1]
        content_type = pr_media.supported_content_type(file_name)
        if content_type is None:
            return {
                "success": False,
                "error": "unsupported media type; supported extensions: "
                + ", ".join(sorted(pr_media.SUPPORTED_CONTENT_TYPES)),
            }
        read = await backend.aexecute(f"base64 -w0 -- {shlex.quote(source_path)}")
        if read.exit_code != 0:
            return {"success": False, "error": "failed to read the sandbox file"}
        try:
            media = pr_media.decode_media_base64(read.output.strip())
        except ValueError:
            return {"success": False, "error": "sandbox file did not decode as media bytes"}
        if not media:
            return {"success": False, "error": "media file is empty"}
        if len(media) > pr_media.MAX_MEDIA_BYTES:
            return {"success": False, "error": "media exceeds GitHub's 100 MB attachment limit"}

        repo_info = await pr_media_support.resolve_repository(
            login, owner=repo.owner, repo=repo.name
        )
        pull_title = await pr_media_support.fetch_pull_title(
            repo_info["token"],
            owner=repo.owner,
            repo=repo.name,
            pull_number=pull_number,
        )
        record, created = await pr_media.create_media_request(
            str(thread_id),
            owner=repo.owner,
            repo=repo.name,
            repo_id=repo_info["repo_id"],
            pull_number=pull_number,
            pull_title=pull_title,
            file_name=file_name,
            content_type=content_type,
            media=media,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("request_pr_media_upload failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to prepare media upload: {exc}"}

    approval_url = dashboard_thread_url(str(thread_id))
    return {
        "success": True,
        "fingerprint": record["fingerprint"],
        "fileName": record["file_name"],
        "sizeBytes": record["size_bytes"],
        "digest": record["digest"],
        "created": created,
        "status": record["status"],
        "approvalUrl": approval_url,
        "note": (
            "The upload requires human approval in the dashboard. Share the approval URL "
            "with the user; do not retry or re-request once it is decided."
        ),
    }


request_pr_media_upload = tool(
    "request_pr_media_upload",
    description=load_prompt("tools/request_pr_media_upload.md"),
)(_request_pr_media_upload)
