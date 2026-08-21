"""Admin authentication for GitHub Actions workflows via OIDC.

A workflow with ``permissions: id-token: write`` can mint a short-lived OIDC
token that GitHub signs and scopes to the repo, ref, and audience it was
requested for. That is a better CI credential than a stored PAT: nothing
long-lived is kept in the calling repo's secrets.

Configuration:

``ADMIN_OIDC_SUBJECTS``
    Comma-separated allowlist, and the on/off switch — empty means this auth path
    is unavailable. An entry containing ``:`` is matched against the token's full
    ``sub`` claim (``repo:acme/images:ref:refs/heads/main``); an ``owner/repo``
    entry matches the ``repository`` claim, allowing any workflow or ref in that
    repo.

``ADMIN_OIDC_AUDIENCE``
    Optional; the audience the workflow must request. Defaults to ``open-swe``.
    Verified either way, so a token minted for another service can't be replayed
    here.

Anyone able to run a workflow in an allowlisted repo (or ref) gets admin on the
endpoints that accept this, so scope the allowlist to internal repos.
"""

import asyncio
import logging
from typing import Any

import jwt
from fastapi import HTTPException

from ..config import admin_oidc_audience, admin_oidc_subjects

logger = logging.getLogger(__name__)

GITHUB_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
DEFAULT_OIDC_AUDIENCE = "open-swe"
_JWKS_URL = f"{GITHUB_ACTIONS_ISSUER}/.well-known/jwks"
_JWKS_CACHE_SECONDS = 600

_jwk_client: jwt.PyJWKClient | None = None


def _client() -> jwt.PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(
            _JWKS_URL, cache_keys=True, cache_jwk_set=True, lifespan=_JWKS_CACHE_SECONDS
        )
    return _jwk_client


def _audience() -> str:
    return admin_oidc_audience() or DEFAULT_OIDC_AUDIENCE


def _allowlist() -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(exact subjects, repositories)`` from ``ADMIN_OIDC_SUBJECTS``."""
    entries = admin_oidc_subjects()
    return (
        frozenset(entry for entry in entries if ":" in entry),
        frozenset(entry.lower() for entry in entries if ":" not in entry),
    )


def actions_oidc_configured() -> bool:
    subjects, repositories = _allowlist()
    return bool(subjects or repositories)


def is_actions_oidc_token(token: str) -> bool:
    """Whether ``token`` claims to be a GitHub Actions OIDC token.

    Routing only — the claim is unverified here and re-checked during decode.
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    return claims.get("iss") == GITHUB_ACTIONS_ISSUER


def _decode(token: str, audience: str) -> dict[str, Any]:
    signing_key = _client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=GITHUB_ACTIONS_ISSUER,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )


def _subject_allowed(claims: dict[str, Any]) -> bool:
    subjects, repositories = _allowlist()
    subject = claims.get("sub")
    if isinstance(subject, str) and subject in subjects:
        return True
    repository = claims.get("repository")
    return isinstance(repository, str) and repository.lower() in repositories


async def admin_session_for_actions_oidc(token: str) -> dict[str, Any]:
    """Return a session-shaped identity for an allowlisted Actions workflow.

    Raises 401 when the token is invalid or this auth path is unconfigured, and
    403 when the token's subject is not allowlisted.
    """
    if not actions_oidc_configured():
        raise HTTPException(401, "GitHub Actions OIDC auth is not configured")
    audience = _audience()

    # PyJWKClient fetches over blocking urllib; keep it off the event loop.
    try:
        claims = await asyncio.to_thread(_decode, token, audience)
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid GitHub Actions OIDC token: {e}") from e
    except Exception as e:  # noqa: BLE001 - JWKS fetch failures
        raise HTTPException(502, f"could not verify GitHub Actions OIDC token: {e}") from e

    if not _subject_allowed(claims):
        logger.warning(
            "Rejected GitHub Actions OIDC request for unlisted subject %r", claims.get("sub")
        )
        raise HTTPException(403, "workflow is not allowed to act as an admin")

    repository = claims.get("repository")
    identity = repository if isinstance(repository, str) and repository else claims.get("sub")
    return {
        "sub": f"actions:{identity}",
        "email": None,
        "auth": "actions_oidc",
        "oidc_subject": claims.get("sub"),
    }
