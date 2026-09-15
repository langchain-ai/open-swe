"""HITL API for uploading agent-generated media to GitHub pull requests.

A thread prepares a media request (file + target PR); the authenticated human
who owns the thread approves that exact request in the dashboard; the upload
then executes server-side with the approver's GitHub OAuth token, which never
enters the shared sandbox.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.dashboard.oauth import require_same_origin_for_mutations, require_session
from agent.github import pr_media, pr_media_support
from agent.threads.plan_api import fetch_thread_metadata
from agent.threads.summary import thread_is_owner, thread_is_promptable, thread_is_readable

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dashboard/api/pr-media",
    tags=["pr-media"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_SESSION_DEP = Depends(require_session)


class MediaRequestCreate(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    pull_number: int = Field(gt=0)
    file_name: str = Field(min_length=1, max_length=255)
    media_base64: str = Field(min_length=4)


def _metadata_or_404(metadata: dict[str, Any]) -> dict[str, Any]:
    if not metadata:
        raise HTTPException(404, "thread not found")
    return metadata


def _require_thread_owner(metadata: dict[str, Any], session: dict[str, Any]) -> str:
    """Media uploads always execute as the thread owner; others cannot decide."""
    if not thread_is_promptable(metadata, session["sub"]) or not thread_is_owner(
        metadata, session["sub"]
    ):
        raise HTTPException(404, "thread not found")
    return str(metadata["owner_login"]).strip()


@router.get("/{thread_id}")
async def list_media_requests(
    thread_id: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = _metadata_or_404(await fetch_thread_metadata(thread_id))
    if not thread_is_readable(metadata, session["sub"], session.get("email")):
        raise HTTPException(404, "thread not found")
    requests = await pr_media.get_media_requests(thread_id)
    return {
        "threadId": thread_id,
        "requests": [
            pr_media.media_request_response(r)
            for r in sorted(
                requests.values(), key=lambda r: str(r.get("requested_at", "")), reverse=True
            )
        ],
    }


@router.post("/{thread_id}/requests")
async def create_media_request(
    thread_id: str, body: MediaRequestCreate, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    """Prepare a pending media request; only the thread owner may prepare."""
    metadata = _metadata_or_404(await fetch_thread_metadata(thread_id))
    preparer_login = _require_thread_owner(metadata, session)
    if not pr_media.valid_repo_part(body.owner) or not pr_media.valid_repo_part(body.repo):
        raise HTTPException(422, "invalid owner or repo")
    content_type = pr_media.supported_content_type(body.file_name)
    if content_type is None:
        raise HTTPException(422, "unsupported media type for this file name")
    if "/" in body.file_name or "\\" in body.file_name or body.file_name in {".", ".."}:
        raise HTTPException(422, "file_name must be a bare file name")
    try:
        media = pr_media.decode_media_base64(body.media_base64)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not media:
        raise HTTPException(422, "media payload is empty")
    if len(media) > pr_media.MAX_MEDIA_BYTES:
        raise HTTPException(413, "media exceeds GitHub's 100 MB attachment limit")

    repo_info = await pr_media_support.resolve_repository(
        preparer_login, owner=body.owner, repo=body.repo
    )
    pull_title = await pr_media_support.fetch_pull_title(
        repo_info["token"], owner=body.owner, repo=body.repo, pull_number=body.pull_number
    )
    record, created = await pr_media.create_media_request(
        thread_id,
        owner=body.owner,
        repo=body.repo,
        repo_id=repo_info["repo_id"],
        pull_number=body.pull_number,
        pull_title=pull_title,
        file_name=body.file_name,
        content_type=content_type,
        media=media,
    )
    logger.info(
        "media request prepared",
        extra={
            "media_fingerprint": record["fingerprint"],
            "thread_id": thread_id,
            "media_created": created,
        },
    )
    return {"request": pr_media.media_request_response(record), "created": created}


@router.post("/{thread_id}/{fingerprint}/approve")
async def approve_media_request(
    thread_id: str, fingerprint: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    """Approve and execute the exact recorded upload as the thread owner.

    Claiming the approval (pending -> approved) is the single execution claim:
    the upload then runs at most once, regardless of how the request ends.
    """
    metadata = _metadata_or_404(await fetch_thread_metadata(thread_id))
    owner_login = _require_thread_owner(metadata, session)
    requests = await pr_media.get_media_requests(thread_id)
    record = requests.get(fingerprint)
    if record is None:
        raise HTTPException(404, "media request not found")
    if record.get("status") == pr_media.MEDIA_REQUEST_COMPLETED:
        raise HTTPException(409, "media request already executed")
    if record.get("status") == pr_media.MEDIA_REQUEST_REJECTED:
        raise HTTPException(409, "media request was rejected")
    if record.get("status") != pr_media.MEDIA_REQUEST_PENDING:
        raise HTTPException(409, "media request is not pending")

    record["status"] = pr_media.MEDIA_REQUEST_APPROVED
    record["decided_by"] = owner_login
    requests[fingerprint] = record
    await pr_media.save_media_requests(thread_id, requests)

    oauth_token = await pr_media_support.get_oauth_token_for_upload(owner_login)
    if oauth_token is None:
        record["status"] = pr_media.MEDIA_REQUEST_PENDING
        record.pop("decided_by", None)
        requests[fingerprint] = record
        await pr_media.save_media_requests(thread_id, requests)
        raise HTTPException(401, "GitHub re-authentication required before uploading")
    try:
        record = await pr_media.execute_approved_media_request(
            thread_id, fingerprint, approver_login=owner_login, oauth_token=oauth_token
        )
    except pr_media.MediaUploadRejected as exc:
        raise HTTPException(403, str(exc)) from exc
    except pr_media.MediaUploadFailed as exc:
        raise HTTPException(exc.status_code or 502, str(exc)) from exc
    return {"request": pr_media.media_request_response(record)}


@router.post("/{thread_id}/{fingerprint}/reject")
async def reject_media_request(
    thread_id: str, fingerprint: str, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, Any]:
    metadata = _metadata_or_404(await fetch_thread_metadata(thread_id))
    owner_login = _require_thread_owner(metadata, session)
    requests = await pr_media.get_media_requests(thread_id)
    record = requests.get(fingerprint)
    if record is None:
        raise HTTPException(404, "media request not found")
    if record.get("status") not in (pr_media.MEDIA_REQUEST_PENDING,):
        raise HTTPException(409, "only pending media requests can be rejected")
    record["status"] = pr_media.MEDIA_REQUEST_REJECTED
    record["decided_by"] = owner_login
    requests[fingerprint] = record
    await pr_media.save_media_requests(thread_id, requests)
    return {"request": pr_media.media_request_response(record)}
