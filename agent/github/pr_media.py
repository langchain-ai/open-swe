"""Human-approved uploads of agent-generated media to GitHub pull requests.

Uploads go through the same endpoint as ``gh --attach`` (cli/cli,
internal/attachments): ``POST https://uploads.github.com/user-attachments/assets``
with the raw bytes as the body, ``name``/``content_type``/``repository_id`` query
parameters, and a 200 JSON ``{"url": ...}`` response. The endpoint accepts only
user OAuth tokens and personal access tokens (classic or fine-grained); GitHub
App installation and user-to-server tokens are rejected, so uploads always
execute server-side with the approving human's OAuth token.
"""

import base64
import binascii
import hashlib
import logging
import re
from collections.abc import Mapping
from typing import Any

import httpx2

from agent.github.http import DEFAULT_TIMEOUT, github_headers
from agent.store import now_iso

logger = logging.getLogger(__name__)

UPLOAD_ENDPOINT = "https://uploads.github.com/user-attachments/assets"
GITHUB_API = "https://api.github.com"

MEDIA_REQUESTS_KEY = "pr_media_requests"
MEDIA_REQUEST_PENDING = "pending"
MEDIA_REQUEST_APPROVED = "approved"
MEDIA_REQUEST_REJECTED = "rejected"
MEDIA_REQUEST_COMPLETED = "completed"
MEDIA_REQUEST_FAILED = "failed"

# Mirrors the web/gh upload limits (10 MB images, 100 MB video on paid plans);
# 100 MB is the upper bound we accept before GitHub's own plan limits apply.
MAX_MEDIA_BYTES = 100 * 1024 * 1024
_MAX_REQUEST_RECORDS = 3
_REQUEST_TTL_SECONDS = 3600

REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

# Credential classes the upload endpoint accepts (mirrors gh's allowlist);
# installation (ghs_) and user-to-server (ghu_) tokens are rejected by it.
_UPLOADABLE_TOKEN_PREFIXES = ("gho_", "ghp_", "github_pat_")


def supported_content_type(file_name: str) -> str | None:
    suffix = file_name.rsplit(".", 1)
    if len(suffix) != 2:
        return None
    return SUPPORTED_CONTENT_TYPES.get(f".{suffix[1].lower()}")


def valid_repo_part(value: str) -> bool:
    return bool(REPO_NAME.fullmatch(value)) and value not in {".", ".."}


def media_request_fingerprint(
    *,
    owner: str,
    repo: str,
    repo_id: int,
    pull_number: int,
    file_name: str,
    content_type: str,
    size_bytes: int,
    digest: str,
) -> str:
    """Content-addressed identity for one exact upload to one exact PR."""
    canonical = "\n".join(
        [
            owner.lower(),
            repo.lower(),
            str(repo_id),
            str(pull_number),
            file_name,
            content_type,
            str(size_bytes),
            digest.lower(),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decode_media_base64(media_base64: str) -> bytes:
    """Strictly decode a base64 media payload, rejecting malformed or padded input."""
    try:
        return base64.b64decode(media_base64.encode("ascii"), validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("media_base64 must be canonical base64") from exc


def _requests_from_metadata(metadata: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = metadata.get(MEDIA_REQUESTS_KEY) if metadata else None
    if not isinstance(raw, dict):
        return {}
    requests: dict[str, dict[str, Any]] = {}
    for fingerprint, value in raw.items():
        if isinstance(fingerprint, str) and fingerprint and isinstance(value, dict):
            record = dict(value)
            record.setdefault("fingerprint", fingerprint)
            requests[fingerprint] = record
    return requests


async def _thread_metadata(thread_id: str) -> dict[str, Any]:
    from langgraph_sdk import get_client

    thread = await get_client().threads.get(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return dict(metadata) if isinstance(metadata, dict) else {}


async def get_media_requests(thread_id: str) -> dict[str, dict[str, Any]]:
    return _requests_from_metadata(await _thread_metadata(thread_id))


async def save_media_requests(thread_id: str, requests: dict[str, dict[str, Any]]) -> None:
    from langgraph_sdk import get_client

    ordered = sorted(requests.values(), key=lambda r: str(r.get("requested_at", "")))
    trimmed = ordered[-_MAX_REQUEST_RECORDS:]
    await get_client().threads.update(
        thread_id=thread_id,
        metadata={MEDIA_REQUESTS_KEY: {str(r["fingerprint"]): r for r in trimmed}},
    )


async def create_media_request(
    thread_id: str,
    *,
    owner: str,
    repo: str,
    repo_id: int,
    pull_number: int,
    pull_title: str,
    file_name: str,
    content_type: str,
    media: bytes,
) -> tuple[dict[str, Any], bool]:
    """Record an immutable pending request; returns (record, created)."""
    digest = hashlib.sha256(media).hexdigest()
    fingerprint = media_request_fingerprint(
        owner=owner,
        repo=repo,
        repo_id=repo_id,
        pull_number=pull_number,
        file_name=file_name,
        content_type=content_type,
        size_bytes=len(media),
        digest=digest,
    )
    requests = await get_media_requests(thread_id)
    existing = requests.get(fingerprint)
    if existing:
        return existing, False
    record = {
        "fingerprint": fingerprint,
        "status": MEDIA_REQUEST_PENDING,
        "owner": owner,
        "repo": repo,
        "repo_id": repo_id,
        "pull_number": pull_number,
        "pull_title": pull_title,
        "file_name": file_name,
        "content_type": content_type,
        "size_bytes": len(media),
        "digest": digest,
        "media_base64": base64.b64encode(media).decode("ascii"),
        "requested_at": now_iso(),
        "expires_at_epoch": _now_epoch() + _REQUEST_TTL_SECONDS,
    }
    requests[fingerprint] = record
    await save_media_requests(thread_id, requests)
    return record, True


def _now_epoch() -> int:
    from datetime import UTC, datetime

    return int(datetime.now(UTC).timestamp())


def _is_expired(record: Mapping[str, Any]) -> bool:
    expires = record.get("expires_at_epoch")
    return isinstance(expires, int | float) and _now_epoch() >= expires


def media_request_response(record: Mapping[str, Any]) -> dict[str, Any]:
    """Public shape for the dashboard; never includes the media payload."""
    asset_url = record.get("asset_url")
    return {
        "fingerprint": str(record.get("fingerprint") or ""),
        "status": str(record.get("status") or MEDIA_REQUEST_PENDING),
        "owner": str(record.get("owner") or ""),
        "repo": str(record.get("repo") or ""),
        "pullNumber": record.get("pull_number")
        if isinstance(record.get("pull_number"), int)
        else 0,
        "pullTitle": str(record.get("pull_title") or ""),
        "fileName": str(record.get("file_name") or ""),
        "contentType": str(record.get("content_type") or ""),
        "sizeBytes": record.get("size_bytes") if isinstance(record.get("size_bytes"), int) else 0,
        "digest": str(record.get("digest") or ""),
        "requestedAt": record.get("requested_at")
        if isinstance(record.get("requested_at"), str)
        else None,
        "expiresAtEpoch": record.get("expires_at_epoch")
        if isinstance(record.get("expires_at_epoch"), int | float)
        else None,
        "decidedBy": record.get("decided_by")
        if isinstance(record.get("decided_by"), str)
        else None,
        "assetUrl": asset_url if isinstance(asset_url, str) and asset_url else None,
        "error": record.get("error") if isinstance(record.get("error"), str) else None,
    }


class MediaUploadRejected(RuntimeError):
    """The token's credential class cannot use the attachment endpoint."""


class MediaUploadFailed(RuntimeError):
    """GitHub refused or failed the upload."""

    def __init__(self, status_code: int | None, detail: str) -> None:
        self.status_code = status_code
        super().__init__(detail)


def assert_uploadable_token(token: str) -> None:
    """Reject credential classes GitHub's attachment endpoint does not accept."""
    if token.startswith(_UPLOADABLE_TOKEN_PREFIXES):
        return
    raise MediaUploadRejected(
        "GitHub only accepts user OAuth tokens or personal access tokens for media "
        "uploads; GitHub App installation tokens are rejected by the upload endpoint."
    )


async def upload_media_asset(
    token: str,
    *,
    repo_id: int,
    file_name: str,
    content_type: str,
    media: bytes,
) -> str:
    """Upload bytes to GitHub's user-attachments surface; returns the asset href."""
    assert_uploadable_token(token)
    async with httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.post(
            UPLOAD_ENDPOINT,
            params={
                "name": file_name,
                "content_type": content_type,
                "repository_id": str(repo_id),
            },
            headers={**github_headers(token), "Content-Type": "application/octet-stream"},
            content=media,
        )
    if response.status_code != 200:
        raise MediaUploadFailed(response.status_code, _github_error_detail(response))
    data = response.json() if response.content else {}
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise MediaUploadFailed(response.status_code, "upload returned no asset URL")
    return url


def _github_error_detail(response: httpx2.Response) -> str:
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict) and isinstance(data.get("message"), str):
        return data["message"]
    return (response.text or "").strip()[:200] or f"HTTP {response.status_code}"


async def append_media_comment(
    token: str,
    *,
    owner: str,
    repo: str,
    pull_number: int,
    file_name: str,
    content_type: str,
    asset_url: str,
) -> None:
    """Post the uploaded asset as a PR conversation comment.

    Videos must be bare URLs to render as players; images embed as Markdown.
    """
    if content_type.startswith("video/"):
        body = f"{asset_url}\n\n_Attached `{file_name}` via approved media upload._"
    else:
        body = f"![{file_name}]({asset_url})\n\n_Attached `{file_name}` via approved media upload._"
    async with httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.post(
            f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pull_number}/comments",
            headers=github_headers(token),
            json={"body": body},
        )
    if response.status_code != 201:
        raise MediaUploadFailed(response.status_code, _github_error_detail(response))


async def execute_approved_media_request(
    thread_id: str,
    fingerprint: str,
    *,
    approver_login: str,
    oauth_token: str,
) -> dict[str, Any]:
    """Run the upload for an approved request, exactly as recorded.

    Enforces expiry, single-use replay protection, payload integrity against
    the approved digest, and ownership by the thread owner (checked by the
    caller). Returns the updated record.
    """
    requests = await get_media_requests(thread_id)
    record = requests.get(fingerprint)
    if record is None:
        raise MediaUploadFailed(None, "media request not found")
    status = record.get("status")
    if status == MEDIA_REQUEST_COMPLETED:
        raise MediaUploadFailed(None, "media request already executed")
    if status == MEDIA_REQUEST_REJECTED:
        raise MediaUploadFailed(None, "media request was rejected")
    if status != MEDIA_REQUEST_APPROVED:
        raise MediaUploadFailed(None, "media request is not approved")
    if record.get("decided_by") != approver_login:
        raise MediaUploadFailed(None, "only the approver's credentials may execute this upload")
    if _is_expired(record):
        record["status"] = MEDIA_REQUEST_FAILED
        record["error"] = "approval expired before execution"
        requests[fingerprint] = record
        await save_media_requests(thread_id, requests)
        raise MediaUploadFailed(None, "approval expired; prepare a new media request")

    encoded = record.get("media_base64")
    if not isinstance(encoded, str) or not encoded:
        raise MediaUploadFailed(None, "media request has no payload")
    media = decode_media_base64(encoded)
    if hashlib.sha256(media).hexdigest() != str(record.get("digest") or "").lower():
        raise MediaUploadFailed(None, "media payload no longer matches the approved digest")

    # Execution is single-use: once any part of the upload starts, the record is
    # never retried, because an ambiguous outcome (network failure after GitHub
    # stored the asset) cannot be distinguished from a genuine refusal.
    try:
        asset_url = await upload_media_asset(
            oauth_token,
            repo_id=int(record["repo_id"]),
            file_name=str(record["file_name"]),
            content_type=str(record["content_type"]),
            media=media,
        )
        await append_media_comment(
            oauth_token,
            owner=str(record["owner"]),
            repo=str(record["repo"]),
            pull_number=int(record["pull_number"]),
            file_name=str(record["file_name"]),
            content_type=str(record["content_type"]),
            asset_url=asset_url,
        )
    except MediaUploadFailed as exc:
        record["status"] = MEDIA_REQUEST_FAILED
        record["error"] = str(exc)
        record["executed_at"] = now_iso()
        requests[fingerprint] = record
        await save_media_requests(thread_id, requests)
        logger.warning(
            "approved media upload failed",
            extra={
                "media_fingerprint": fingerprint,
                "thread_id": thread_id,
                "http_status": exc.status_code,
            },
        )
        raise

    record["status"] = MEDIA_REQUEST_COMPLETED
    record["asset_url"] = asset_url
    record["executed_at"] = now_iso()
    record.pop("error", None)
    requests[fingerprint] = record
    await save_media_requests(thread_id, requests)
    logger.info(
        "approved media upload completed",
        extra={"media_fingerprint": fingerprint, "thread_id": thread_id},
    )
    return record
