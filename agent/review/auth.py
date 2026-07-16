from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from ..utils.github_app import (
    get_github_app_installation_token_with_expiry,
    invalidate_cached_app_token,
)
from ..utils.github_token import cache_github_token_for_thread
from .findings import (
    REVIEWER_THREAD_KIND,
    ReviewerThreadMissingError,
    get_thread_metadata_strict,
    get_thread_pr_meta,
)

logger = logging.getLogger(__name__)

REVIEWER_GITHUB_TOKEN_PERMISSIONS = {
    "checks": "write",
    "contents": "read",
    "pull_requests": "write",
}
_MAX_TOKEN_MINT_ATTEMPTS = 3
_TOKEN_MINT_INITIAL_DELAY_SECONDS = 0.25


class ReviewerPublicationContextError(Exception):
    """Raised when durable reviewer metadata does not match the publish target."""


class ReviewerPublicationContextUnavailableError(Exception):
    """Raised when durable reviewer context cannot be verified transiently."""


class ReviewerTokenUnavailableError(Exception):
    """Raised when a reviewer GitHub App token cannot be provisioned."""


async def mint_reviewer_github_token(
    *,
    thread_id: str,
    repo: str,
    attempts: int = 1,
) -> str | None:
    """Mint and process-cache a least-privilege reviewer token."""
    if not repo:
        return None
    bounded_attempts = max(1, min(attempts, _MAX_TOKEN_MINT_ATTEMPTS))
    for attempt in range(bounded_attempts):
        token, expires_at = await get_github_app_installation_token_with_expiry(
            repositories=[repo],
            permissions=REVIEWER_GITHUB_TOKEN_PERMISSIONS,
        )
        if token:
            cache_github_token_for_thread(
                thread_id,
                token,
                expires_at=expires_at,
                is_bot_token=True,
            )
            return token
        if attempt + 1 < bounded_attempts:
            await asyncio.sleep(_TOKEN_MINT_INITIAL_DELAY_SECONDS * (2**attempt))
    return None


def invalidate_reviewer_github_token(repo: str) -> None:
    """Invalidate the exact reviewer App-token scope after a 401."""
    invalidate_cached_app_token(
        repositories=[repo],
        permissions=REVIEWER_GITHUB_TOKEN_PERMISSIONS,
    )


async def recover_reviewer_github_token(
    *,
    run_config: Mapping[str, Any],
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    run_id: str | None,
) -> str:
    """Recover reviewer auth at the publish boundary after a durable resume."""
    configurable = run_config.get("configurable", {})
    configured_thread_id = (
        configurable.get("thread_id") if isinstance(configurable, Mapping) else None
    )
    if configured_thread_id != thread_id:
        raise ReviewerPublicationContextError("reviewer thread context mismatch")

    try:
        metadata = await get_thread_metadata_strict(thread_id)
    except ReviewerThreadMissingError:
        raise
    except Exception as exc:
        logger.warning(
            "Reviewer publication context unavailable for thread %s",
            thread_id,
            exc_info=True,
        )
        raise ReviewerPublicationContextUnavailableError(
            "reviewer publication context unavailable"
        ) from exc
    pr = get_thread_pr_meta(metadata)
    if (
        metadata.get("kind") != REVIEWER_THREAD_KIND
        or pr is None
        or str(pr.get("owner", "")).casefold() != owner.casefold()
        or str(pr.get("name", "")).casefold() != repo.casefold()
        or pr.get("number") != pr_number
    ):
        logger.warning(
            "Refusing reviewer token recovery after publication context mismatch for thread %s",
            thread_id,
        )
        raise ReviewerPublicationContextError("reviewer publication context mismatch")

    current_run_id = metadata.get("current_reviewer_run_id")
    if not isinstance(current_run_id, str) or not current_run_id:
        logger.warning(
            "Refusing reviewer token recovery without an active run for thread %s",
            thread_id,
        )
        raise ReviewerPublicationContextUnavailableError("active reviewer run unavailable")
    if run_id and current_run_id != run_id:
        logger.warning(
            "Refusing reviewer token recovery for stale run on thread %s",
            thread_id,
        )
        raise ReviewerPublicationContextError("reviewer run is not current")

    token = await mint_reviewer_github_token(
        thread_id=thread_id,
        repo=repo,
        attempts=_MAX_TOKEN_MINT_ATTEMPTS,
    )
    if not token:
        logger.error(
            "Reviewer token recovery failed for thread %s repo %s/%s PR %s",
            thread_id,
            owner,
            repo,
            pr_number,
        )
        raise ReviewerTokenUnavailableError("GitHub App installation token unavailable")

    logger.info(
        "Recovered reviewer GitHub App token for thread %s repo %s/%s PR %s",
        thread_id,
        owner,
        repo,
        pr_number,
    )
    return token
