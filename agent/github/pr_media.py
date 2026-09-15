"""Human-approved uploads of agent-generated media to GitHub pull requests.

Uploads go through the same endpoint as ``gh --attach`` (cli/cli,
internal/attachments): ``POST https://uploads.github.com/user-attachments/assets``
with the raw bytes as the body, ``name``/``content_type``/``repository_id`` query
parameters, and a 200 JSON ``{"url": ...}`` response. The endpoint accepts only
user OAuth tokens and personal access tokens (classic or fine-grained); GitHub
App installation and user-to-server tokens are rejected, so uploads always
execute server-side with the approving human's OAuth token.

Requests are rows in ``pr_media_request``. Approval is a single-statement
compare-and-set (``pending -> approved``), so exactly one caller claims
execution; claim, expiry, and replay rules are enforced by the database, not by
read-modify-write on thread metadata.
"""

import base64
import binascii
import hashlib
import logging
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

import httpx2
from sqlalchemy import text

from agent.database import postgres
from agent.github.http import DEFAULT_TIMEOUT, github_headers

logger = logging.getLogger(__name__)

UPLOAD_ENDPOINT = "https://uploads.github.com/user-attachments/assets"
GITHUB_API = "https://api.github.com"

MEDIA_REQUEST_PENDING = "pending"
MEDIA_REQUEST_APPROVED = "approved"
MEDIA_REQUEST_REJECTED = "rejected"
MEDIA_REQUEST_COMPLETED = "completed"
MEDIA_REQUEST_FAILED = "failed"
MediaRequestStatus = Literal["pending", "approved", "rejected", "completed", "failed"]

# Mirrors the web/gh upload limits (10 MB images, 100 MB video on paid plans);
# 100 MB is the upper bound we accept before GitHub's own plan limits apply.
MAX_MEDIA_BYTES = 100 * 1024 * 1024
REQUEST_TTL_SECONDS = 3600

REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")

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

_TABLE = "pr_media_request"
_COLUMNS = (
    "fingerprint, thread_id, status, owner, repo, repo_id, pull_number, pull_title,"
    " file_name, content_type, size_bytes, digest, media_base64, requested_by,"
    " requested_at, expires_at_epoch, decided_by, decided_at, asset_url, error"
)


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


def now_epoch() -> int:
    from datetime import UTC

    return int(datetime.now(UTC).timestamp())


def _row_to_record(row: Any) -> dict[str, Any]:
    return {str(key): value for key, value in dict(row).items()}


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
    requested_by: str,
) -> tuple[dict[str, Any], bool]:
    """Insert the immutable pending request; returns (record, created).

    The fingerprint primary key makes identical re-requests idempotent: a
    duplicate returns the existing record without touching its state.
    """
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
    async with postgres.transaction() as conn:
        inserted = (
            await conn.execute(
                text(
                    f"""
                INSERT INTO {_TABLE} (
                    fingerprint, thread_id, status, owner, repo, repo_id, pull_number,
                    pull_title, file_name, content_type, size_bytes, digest,
                    media_base64, requested_by, requested_at, expires_at_epoch
                ) VALUES (
                    :fingerprint, :thread_id, :status, :owner, :repo, :repo_id,
                    :pull_number, :pull_title, :file_name, :content_type, :size_bytes,
                    :digest, :media_base64, :requested_by, clock_timestamp(),
                    :expires_at_epoch
                )
                ON CONFLICT (fingerprint) DO NOTHING
                """
                ),
                {
                    "fingerprint": fingerprint,
                    "thread_id": thread_id,
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
                    "requested_by": requested_by,
                    "expires_at_epoch": now_epoch() + REQUEST_TTL_SECONDS,
                },
            )
        ).rowcount
        row = (
            (
                await conn.execute(
                    text(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE fingerprint = :fingerprint"),
                    {"fingerprint": fingerprint},
                )
            )
            .mappings()
            .one()
        )
    return _row_to_record(row), inserted == 1


async def get_media_request(thread_id: str, fingerprint: str) -> dict[str, Any] | None:
    """The request, but only when it belongs to this thread."""
    if not FINGERPRINT.fullmatch(fingerprint):
        return None
    async with postgres.connection() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM {_TABLE}"
                        " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                    ),
                    {"fingerprint": fingerprint, "thread_id": thread_id},
                )
            )
            .mappings()
            .first()
        )
    return _row_to_record(row) if row else None


async def list_media_requests(thread_id: str) -> list[dict[str, Any]]:
    async with postgres.connection() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM {_TABLE} WHERE thread_id = :thread_id"
                        " ORDER BY requested_at DESC LIMIT 25"
                    ),
                    {"thread_id": thread_id},
                )
            )
            .mappings()
            .all()
        )
    return [_row_to_record(row) for row in rows]


async def claim_media_request(thread_id: str, fingerprint: str, *, actor: str) -> str:
    """Atomic compare-and-set: claim a pending request for execution.

    Returns the resulting status transition outcome: ``claimed`` when this
    caller won the race, ``expired`` when the request lapsed, ``conflict``
    when it is already decided, ``missing`` when it does not exist.
    """
    if not FINGERPRINT.fullmatch(fingerprint):
        return "missing"
    now = now_epoch()
    async with postgres.transaction() as conn:
        claimed = (
            await conn.execute(
                text(
                    f"UPDATE {_TABLE} SET status = :approved, decided_by = :actor,"
                    " decided_at = clock_timestamp()"
                    " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                    " AND status = :pending AND expires_at_epoch > :now"
                ),
                {
                    "approved": MEDIA_REQUEST_APPROVED,
                    "actor": actor,
                    "fingerprint": fingerprint,
                    "thread_id": thread_id,
                    "pending": MEDIA_REQUEST_PENDING,
                    "now": now,
                },
            )
        ).rowcount
        if claimed == 1:
            return "claimed"
        row = (
            (
                await conn.execute(
                    text(
                        f"SELECT status, expires_at_epoch FROM {_TABLE}"
                        " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                    ),
                    {"fingerprint": fingerprint, "thread_id": thread_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return "missing"
        if row["status"] == MEDIA_REQUEST_PENDING and int(row["expires_at_epoch"]) <= now:
            await conn.execute(
                text(
                    f"UPDATE {_TABLE} SET status = :failed,"
                    " error = 'approval expired before a decision'"
                    " WHERE fingerprint = :fingerprint AND status = :pending"
                ),
                {
                    "failed": MEDIA_REQUEST_FAILED,
                    "fingerprint": fingerprint,
                    "pending": MEDIA_REQUEST_PENDING,
                },
            )
            return "expired"
        return "conflict"


async def reject_media_request(thread_id: str, fingerprint: str, *, actor: str) -> str:
    """Atomic compare-and-set: pending -> rejected. Same outcomes as claim."""
    if not FINGERPRINT.fullmatch(fingerprint):
        return "missing"
    async with postgres.transaction() as conn:
        rejected = (
            await conn.execute(
                text(
                    f"UPDATE {_TABLE} SET status = :rejected, decided_by = :actor,"
                    " decided_at = clock_timestamp()"
                    " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                    " AND status = :pending"
                ),
                {
                    "rejected": MEDIA_REQUEST_REJECTED,
                    "actor": actor,
                    "fingerprint": fingerprint,
                    "thread_id": thread_id,
                    "pending": MEDIA_REQUEST_PENDING,
                },
            )
        ).rowcount
        if rejected == 1:
            return "rejected"
        row = (
            (
                await conn.execute(
                    text(
                        f"SELECT status FROM {_TABLE}"
                        " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                    ),
                    {"fingerprint": fingerprint, "thread_id": thread_id},
                )
            )
            .mappings()
            .first()
        )
        return "missing" if row is None else "conflict"


async def release_media_request_claim(thread_id: str, fingerprint: str, *, actor: str) -> None:
    """Return a claim to pending when execution cannot start (re-auth required).

    Compare-and-set on (approved, actor): only the claim holder may release,
    and only a claim that never reached GitHub is released, so replay is safe.
    """
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                f"UPDATE {_TABLE} SET status = :pending, decided_by = NULL, decided_at = NULL"
                " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                " AND status = :approved AND decided_by = :actor"
            ),
            {
                "pending": MEDIA_REQUEST_PENDING,
                "approved": MEDIA_REQUEST_APPROVED,
                "fingerprint": fingerprint,
                "thread_id": thread_id,
                "actor": actor,
            },
        )


async def finish_media_request(
    thread_id: str,
    fingerprint: str,
    *,
    actor: str,
    status: Literal["completed", "failed"],
    asset_url: str | None = None,
    error: str | None = None,
) -> None:
    """Terminal write after execution; only the claim holder may finish."""
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                f"UPDATE {_TABLE} SET status = :status, asset_url = :asset_url,"
                " error = :error"
                " WHERE fingerprint = :fingerprint AND thread_id = :thread_id"
                " AND status = :approved AND decided_by = :actor"
            ),
            {
                "status": status,
                "asset_url": asset_url,
                "error": error,
                "approved": MEDIA_REQUEST_APPROVED,
                "fingerprint": fingerprint,
                "thread_id": thread_id,
                "actor": actor,
            },
        )


def media_request_response(record: Mapping[str, Any]) -> dict[str, Any]:
    """Public shape for the dashboard; never includes the media payload."""
    asset_url = record.get("asset_url")
    requested_at = record.get("requested_at")
    decided_at = record.get("decided_at")
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
        "requestedBy": record.get("requested_by")
        if isinstance(record.get("requested_by"), str)
        else None,
        "requestedAt": requested_at.isoformat() if isinstance(requested_at, datetime) else None,
        "expiresAtEpoch": record.get("expires_at_epoch")
        if isinstance(record.get("expires_at_epoch"), int | float)
        else None,
        "decidedBy": record.get("decided_by")
        if isinstance(record.get("decided_by"), str)
        else None,
        "decidedAt": decided_at.isoformat() if isinstance(decided_at, datetime) else None,
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
    if not isinstance(url, str) or not url.startswith(("https://", "http://127.0.0.1")):
        detail = f"upload returned no asset URL (status={response.status_code}, body={response.text[:120]!r})"
        raise MediaUploadFailed(response.status_code, detail)
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
    """Run the upload for a claimed request, exactly as recorded.

    The caller must have claimed the request via ``claim_media_request`` first;
    this function re-reads the row and enforces expiry, single-use replay
    protection, payload integrity against the approved digest, and approver
    identity before touching GitHub. Returns the updated record.
    """
    record = await get_media_request(thread_id, fingerprint)
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
    expires = record.get("expires_at_epoch")
    if isinstance(expires, int | float) and now_epoch() >= expires:
        await finish_media_request(
            thread_id,
            fingerprint,
            actor=approver_login,
            status="failed",
            error="approval expired before execution",
        )
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
        assert_uploadable_token(oauth_token)
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
    except MediaUploadRejected:
        # Credential class the endpoint rejects: nothing reached GitHub, so the
        # claim is safe to return for another attempt after re-auth.
        await release_media_request_claim(thread_id, fingerprint, actor=approver_login)
        raise
    except MediaUploadFailed as exc:
        await finish_media_request(
            thread_id,
            fingerprint,
            actor=approver_login,
            status="failed",
            error=str(exc),
        )
        logger.warning(
            "approved media upload failed",
            extra={
                "media_fingerprint": fingerprint,
                "thread_id": thread_id,
                "http_status": exc.status_code,
            },
        )
        raise

    await finish_media_request(
        thread_id,
        fingerprint,
        actor=approver_login,
        status="completed",
        asset_url=asset_url,
    )
    logger.info(
        "approved media upload completed",
        extra={"media_fingerprint": fingerprint, "thread_id": thread_id},
    )
    updated = await get_media_request(thread_id, fingerprint)
    assert updated is not None
    return updated
