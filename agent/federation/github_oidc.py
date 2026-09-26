"""Verifying the OIDC token a GitHub Actions workflow issues itself.

A workflow with ``id-token: write`` asks GitHub for a short-lived JWT naming the
repository, ref and workflow that requested it, and presents that instead of a
stored secret. GitHub signs it, so verification is a signature check against
GitHub's published keys plus a look at the claims; nothing here decides what the
caller may do, only who it is. The trust policy lives with the workspace that
granted the repository its thread-starting rights.
"""

import asyncio
import logging

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict

from agent.config import ENV
from agent.utils.dashboard_links import dashboard_base_url

logger = logging.getLogger(__name__)

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
_ALGORITHMS = ("RS256",)
_KEY_CACHE_SECONDS = 900

_jwks: PyJWKClient | None = None


class InvalidFederatedToken(Exception):
    """The presented token is not a GitHub Actions token this deployment accepts."""


class GitHubActionsClaims(BaseModel):
    """What the token says about the workflow run that asked for it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    sub: str
    repository: str
    repository_owner: str = ""
    # "public", "private" or "internal"; an absent claim is treated as public.
    repository_visibility: str = ""
    ref: str = ""
    workflow_ref: str = ""
    run_id: str = ""

    @property
    def workflow(self) -> str:
        """The workflow file, without the repository and ref around it."""
        path = self.workflow_ref.split("@", 1)[0]
        _, _, tail = path.partition("/")
        return tail or path


def audience() -> str:
    """The ``aud`` a workflow must request, so a token minted for somebody else is refused."""
    configured = ENV.GITHUB_OIDC_AUDIENCE.get("").strip()
    return configured or dashboard_base_url()


def _keys() -> PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=_KEY_CACHE_SECONDS)
    return _jwks


def looks_federated(token: str) -> bool:
    """Whether ``token`` claims to be one of GitHub's, before anything is verified.

    Only routing: a request carrying a dashboard session JWT must not be checked
    against GitHub's keys, and one carrying GitHub's must not be decoded as a
    session. The answer is never trusted beyond choosing which verifier runs.
    """
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    return unverified.get("iss") == ISSUER


def _verify(token: str, expected_audience: str) -> GitHubActionsClaims:
    key = _keys().get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key,
        algorithms=list(_ALGORITHMS),
        audience=expected_audience,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    return GitHubActionsClaims.model_validate(claims)


async def verify(token: str) -> GitHubActionsClaims:
    """The workflow behind ``token``, or raise.

    Runs in a thread because fetching and caching GitHub's keys is synchronous
    I/O inside PyJWT.
    """
    expected = audience()
    if not expected:
        raise InvalidFederatedToken(
            "federated tokens need an audience: set GITHUB_OIDC_AUDIENCE or DASHBOARD_BASE_URL"
        )
    try:
        claims = await asyncio.to_thread(_verify, token, expected)
    except jwt.PyJWTError as exc:
        # The message names the check that failed (expiry, audience, signature),
        # which is what makes a misconfigured workflow diagnosable; the caller
        # turns it into one opaque 401.
        raise InvalidFederatedToken(str(exc)) from exc
    if not claims.repository.strip():
        raise InvalidFederatedToken("token names no repository")
    return claims
