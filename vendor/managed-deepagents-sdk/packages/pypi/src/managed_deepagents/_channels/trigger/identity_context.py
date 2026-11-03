"""Verify Trigger Server ``identity_context`` JWTs into Agent Auth principal ids.

Agent Auth signs these with the LangSmith JWKS (any JOSE asymmetric algorithm, chosen by
the key) published at ``{LANGSMITH_ENDPOINT}/.well-known/jwks.json``. MDA verifies the signature
(and typ/version/aud/iss/exp/principal UUID) before stamping ``x-mda-user-id``.
Delivery still requires Trigger's ``x-service-key`` header; signature verify is
what stops a forged principal when that gate is weak.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt import PyJWK, PyJWKClient

from managed_deepagents._langsmith_env import resolve_langsmith_endpoint

IdentityContextKey = Ed25519PublicKey | RSAPublicKey | EllipticCurvePublicKey | PyJWK

IDENTITY_CONTEXT_TOKEN_TYP = "identity-context+jwt"  # noqa: S105
IDENTITY_CONTEXT_AUDIENCE = "mda"
IDENTITY_CONTEXT_VERSION = "1"
IDENTITY_CONTEXT_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)
_JWKS_HTTP_TIMEOUT_S = 5

_jwks_clients: dict[str, PyJWKClient] = {}


class InvalidIdentityContextError(ValueError):
    """Raised when a Trigger ``identity_context`` token is unusable."""


class IdentityContextUnavailableError(RuntimeError):
    """JWKS / network failure while verifying identity_context. Callers should 5xx."""


def resolve_identity_context_issuer(
    env: Mapping[str, str] | None = None,
) -> str:
    """Canonical issuer; self-hosted API URLs may use an internal host or prefix."""
    source = env if env is not None else os.environ
    raw = (source.get("OAUTH_AS_ISSUER") or "").strip() or resolve_langsmith_endpoint(source)
    return raw.rstrip("/")


def resolve_identity_context_jwks_url(
    env: Mapping[str, str] | None = None,
) -> str:
    """Public JWKS URL for Agent Auth identity-context signatures."""
    return f"{resolve_identity_context_issuer(env)}/.well-known/jwks.json"


def principal_id_from_identity_context(
    token: str,
    *,
    key: IdentityContextKey | None = None,
    issuer: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Extract the Agent Auth principal UUID from a Trigger identity-context JWT.

    Raises:
        InvalidIdentityContextError: when the token is missing, malformed, or
            fails signature / claim checks.
    """
    value = token.strip()
    if not value:
        raise InvalidIdentityContextError("identity_context is blank")

    _require_identity_context_header(value)
    claims = _verify_identity_context_claims(
        value,
        key=key,
        issuer=issuer or resolve_identity_context_issuer(env),
        env=env,
    )
    if claims.get("version") != IDENTITY_CONTEXT_VERSION:
        raise InvalidIdentityContextError(
            f"identity_context version must be {IDENTITY_CONTEXT_VERSION}"
        )
    return _principal_id_from_claims(claims)


def optional_principal_id_from_identity_context(
    token: object | None,
    *,
    key: IdentityContextKey | None = None,
    issuer: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a principal id from an optional event field; ``None`` when absent."""
    if token is None:
        return None
    if not isinstance(token, str):
        raise InvalidIdentityContextError("identity_context must be a string")
    if not token.strip():
        return None
    return principal_id_from_identity_context(token, key=key, issuer=issuer, env=env)


def _require_identity_context_header(token: str) -> None:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as error:
        raise InvalidIdentityContextError("identity_context is not a JWT") from error
    if header.get("typ") != IDENTITY_CONTEXT_TOKEN_TYP:
        raise InvalidIdentityContextError(
            f"identity_context typ must be {IDENTITY_CONTEXT_TOKEN_TYP}"
        )
    if header.get("alg") not in IDENTITY_CONTEXT_ALGORITHMS:
        raise InvalidIdentityContextError(
            "identity_context alg must be an asymmetric JOSE algorithm"
        )


def _verify_identity_context_claims(
    token: str,
    *,
    key: IdentityContextKey | None,
    issuer: str,
    env: Mapping[str, str] | None,
) -> dict[str, object]:
    algorithms = list(IDENTITY_CONTEXT_ALGORITHMS)
    try:
        verify_key = _jwks_client(env).get_signing_key_from_jwt(token) if key is None else key
        decoded = jwt.decode(
            token,
            key=verify_key,
            algorithms=algorithms,
            audience=IDENTITY_CONTEXT_AUDIENCE,
            issuer=issuer,
            options={"require": ["exp"], "verify_exp": True},
        )
    except jwt.exceptions.ExpiredSignatureError as error:
        raise InvalidIdentityContextError("identity_context is expired") from error
    except jwt.exceptions.MissingRequiredClaimError as error:
        raise InvalidIdentityContextError("identity_context is missing exp") from error
    except jwt.exceptions.PyJWKClientConnectionError as error:
        raise IdentityContextUnavailableError(
            "identity_context JWKS verification is unavailable"
        ) from error
    except TimeoutError as error:
        raise IdentityContextUnavailableError(
            "identity_context JWKS verification is unavailable"
        ) from error
    except OSError as error:
        raise IdentityContextUnavailableError(
            "identity_context JWKS verification is unavailable"
        ) from error
    except jwt.exceptions.PyJWTError as error:
        raise InvalidIdentityContextError(
            "identity_context signature verification failed"
        ) from error
    if not isinstance(decoded, dict):
        raise InvalidIdentityContextError("identity_context claims must be an object")
    claims = {str(name): value for name, value in decoded.items()}
    if not isinstance(claims.get("exp"), (int, float)) or isinstance(claims.get("exp"), bool):
        raise InvalidIdentityContextError("identity_context is missing exp")
    return claims


def _jwks_client(env: Mapping[str, str] | None) -> PyJWKClient:
    uri = resolve_identity_context_jwks_url(env)
    client = _jwks_clients.get(uri)
    if client is None:
        client = PyJWKClient(uri, timeout=_JWKS_HTTP_TIMEOUT_S)
        _jwks_clients[uri] = client
    return client


def _principal_id_from_claims(claims: Mapping[str, object]) -> str:
    from_principal = _principal_id_claim(claims.get("principal"))
    raw_subject = claims.get("sub")
    from_subject = _normalize_uuid(raw_subject) if isinstance(raw_subject, str) else None
    if from_principal and from_subject and from_principal != from_subject:
        raise InvalidIdentityContextError("identity_context principal.id and sub disagree")
    principal_id = from_principal or from_subject
    if not principal_id:
        raise InvalidIdentityContextError("identity_context is missing a principal UUID")
    return principal_id


def _principal_id_claim(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    raw_id = value.get("id")
    return _normalize_uuid(raw_id) if isinstance(raw_id, str) else None


def _normalize_uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


__all__ = [
    "IDENTITY_CONTEXT_ALGORITHMS",
    "IDENTITY_CONTEXT_AUDIENCE",
    "IDENTITY_CONTEXT_TOKEN_TYP",
    "IDENTITY_CONTEXT_VERSION",
    "IdentityContextKey",
    "IdentityContextUnavailableError",
    "InvalidIdentityContextError",
    "optional_principal_id_from_identity_context",
    "principal_id_from_identity_context",
    "resolve_identity_context_issuer",
    "resolve_identity_context_jwks_url",
]
