"""Verify browser-direct IdP tokens for the ``validated_token`` ingress mode.

Consumed by :func:`managed_deepagents._identity_auth.build_managed_auth`. Picks a
provider by token issuer and verifies it via JWKS local verification or
opaque-token introspection, then maps claims into the identity
envelope. ``pyjwt`` is imported lazily so authoring-only installs stay light.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from managed_deepagents._identity_auth import ManagedAuthError, ManagedUser
from managed_deepagents.identity.claims import (
    normalize_groups,
    user_claim_name,
)

if TYPE_CHECKING:
    from managed_deepagents.identity import (
        IdentityConfig,
        TokenIntrospection,
        ValidatedTokenIngress,
        ValidatedTokenProvider,
    )


class _JwksSigningKey(Protocol):
    """Signing key object returned by ``PyJWKClient.get_signing_key_from_jwt``."""

    @property
    def key(self) -> object: ...


class _JwksClient(Protocol):
    """``jwt.PyJWKClient`` instance used for JWKS verification."""

    def get_signing_key_from_jwt(self, token: str) -> _JwksSigningKey: ...


class _JwksClientFactory(Protocol):
    """``jwt.PyJWKClient`` constructor, called with an explicit request timeout."""

    def __call__(self, uri: str, *, timeout: int = ...) -> _JwksClient: ...


class PyJWTModule(Protocol):
    """Subset of PyJWT used by validated-token verification."""

    def encode(
        self,
        payload: Mapping[str, object],
        key: str,
        algorithm: str = ...,
        headers: Mapping[str, object] | None = None,
    ) -> str: ...

    def decode(
        self,
        jwt: str,
        key: object = ...,
        algorithms: Sequence[str] | None = None,
        options: Mapping[str, object] | None = None,
        audience: str | None = None,
        issuer: str | None = None,
    ) -> Mapping[str, object]: ...

    def get_unverified_header(self, jwt: str) -> Mapping[str, object]: ...

    #: Constructed as ``jwt.PyJWKClient(uri, timeout=...)`` at the JWKS boundary.
    PyJWKClient: _JwksClientFactory


#: Wall-clock budget for auth-path IdP calls (OIDC discovery, introspection), in
#: seconds. A hung IdP must fail closed rather than pin the request slot.
_AUTH_HTTP_TIMEOUT_S = 10
#: Wall-clock budget for remote JWKS fetches, in seconds. Mirrors the npm
#: runtime's ``createRemoteJWKSet`` ``timeoutDuration``.
_JWKS_HTTP_TIMEOUT_S = 5

#: OIDC ``discover`` results (issuer → jwks_uri), cached for the process lifetime.
_discover_cache: dict[str, str] = {}
#: OIDC discovery base → issuer, cached for the process lifetime.
_oidc_issuer_cache: dict[str, str] = {}
#: ``PyJWKClient`` instances, cached per resolved JWKS URI.
_jwks_clients: dict[str, _JwksClient] = {}


def resolve_validated_token_user(
    _cfg: IdentityConfig,
    ingress: ValidatedTokenIngress,
    headers: Mapping[str, object],
    authorization: str | None,
) -> ManagedUser:
    """Resolve the caller identity from a verified browser-direct token.

    OIDC discovery runs only for the selected provider, or for issuer matching
    when no provider is identifiable from already-known config. A discovery
    outage on an unrelated provider does not block authentication.
    """
    token = _bearer_token(headers, authorization)
    provider = resolve_provider(list(ingress.get("providers", [])), token)
    claims = _verify_provider_claims(provider, token, headers)
    return claims_to_user(provider, claims)


def resolve_provider(providers: list[ValidatedTokenProvider], token: str) -> ValidatedTokenProvider:
    """Select the provider for ``token``, discovering OIDC metadata only when needed.

    Prefer providers whose issuer is already known so a custom-domain discovery
    outage cannot block unrelated auth.
    """
    known = _try_select_known_provider(providers, token)
    if known is not None:
        return _materialize_provider(known)

    prepared: list[ValidatedTokenProvider] = []
    for provider in providers:
        try:
            prepared.append(_materialize_provider(provider))
        except Exception:  # noqa: BLE001 - isolate any discovery failure (network/parse)
            # Keep the undeclared provider so other entries can still match.
            prepared.append(provider)
    return select_provider(prepared, token)


def _materialize_provider(provider: ValidatedTokenProvider) -> ValidatedTokenProvider:
    discovery_url = provider.get("discovery_url")
    if not isinstance(discovery_url, str) or not discovery_url:
        return provider
    if provider.get("issuer") and (provider.get("jwks") or provider.get("introspect")):
        return provider
    discovered = _discover_oidc(discovery_url)
    materialized: ValidatedTokenProvider = {
        **provider,
        "issuer": discovered["issuer"],
    }
    if not materialized.get("jwks") and not materialized.get("introspect"):
        materialized["jwks"] = discovered["jwks"]
    return materialized


def _discover_oidc(auth_base: str) -> dict[str, str]:
    base = auth_base.rstrip("/")
    cached_issuer = _oidc_issuer_cache.get(base)
    cached_jwks = _discover_cache.get(base)
    if cached_issuer and cached_jwks:
        return {"issuer": cached_issuer, "jwks": cached_jwks}
    try:
        config = _http_get_json(f"{base}/.well-known/openid-configuration", {})
        issuer = config.get("issuer")
        jwks_uri = config.get("jwks_uri")
    except ManagedAuthError:
        raise
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError) as error:
        # Malformed JSON / unexpected shape must stay auth failures, not 500s,
        # and must be isolatable by :func:`resolve_provider`.
        raise ManagedAuthError(401, "failed to resolve OIDC configuration") from error
    if not isinstance(issuer, str) or not issuer:
        raise ManagedAuthError(401, "OIDC configuration has no issuer")
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise ManagedAuthError(401, "OIDC configuration has no jwks_uri")
    _oidc_issuer_cache[base] = issuer
    _discover_cache[base] = jwks_uri
    # Also cache under the discovered issuer for the existing discover=True path.
    _discover_cache[issuer] = jwks_uri
    return {"issuer": issuer, "jwks": jwks_uri}


def _provider_accepts_issuer(provider: ValidatedTokenProvider, issuer: str) -> bool:
    if provider.get("issuer") == issuer:
        return True
    extra = provider.get("issuers")
    return isinstance(extra, list) and issuer in extra


def _configured_issuers(providers: list[ValidatedTokenProvider]) -> list[str]:
    configured: list[str] = []
    seen: set[str] = set()
    for provider in providers:
        values: list[object] = [provider.get("issuer")]
        extra = provider.get("issuers")
        if isinstance(extra, list):
            values.extend(extra)
        for value in values:
            if isinstance(value, str) and value not in seen:
                seen.add(value)
                configured.append(value)
    return configured


def _sole_introspect_provider(
    providers: list[ValidatedTokenProvider],
) -> ValidatedTokenProvider | None:
    introspect_providers = [provider for provider in providers if provider.get("introspect")]
    return introspect_providers[0] if len(introspect_providers) == 1 else None


def _try_select_known_provider(
    providers: list[ValidatedTokenProvider], token: str
) -> ValidatedTokenProvider | None:
    """Select using already-known issuer/introspect config only.

    Returns ``None`` when selection needs OIDC discovery (e.g. custom-domain
    Supabase whose JWT ``iss`` is not yet on the provider).
    """
    if len(providers) == 1:
        return providers[0]
    issuer = _try_unverified_issuer(token)
    if issuer is None:
        return _sole_introspect_provider(providers)
    for provider in providers:
        if _provider_accepts_issuer(provider, issuer):
            return provider
    return None


def select_provider(providers: list[ValidatedTokenProvider], token: str) -> ValidatedTokenProvider:
    """Select the provider that verifies ``token``.

    Single-provider deployments always use that entry. Otherwise JWT tokens route
    by ``iss``; opaque tokens route to the sole ``introspect`` provider.
    Fail-closed when selection is ambiguous.

    Callers that may need OIDC discovery should use :func:`resolve_provider`.
    """
    known = _try_select_known_provider(providers, token)
    if known is not None:
        return known
    issuer = _try_unverified_issuer(token)
    if issuer is None:
        has_introspect = any(provider.get("introspect") for provider in providers)
        raise ManagedAuthError(
            401,
            "malformed token"
            if not has_introspect
            else "opaque token requires exactly one introspection provider",
        )
    configured = _configured_issuers(providers)
    raise ManagedAuthError(
        401,
        f"no provider matches token issuer {issuer!r}; configured={configured!r}",
    )


def claims_to_user(provider: ValidatedTokenProvider, claims: Mapping[str, object]) -> ManagedUser:
    """Map verified token claims into the managed user envelope."""
    claim_map = provider.get("claims", {})
    user_claim = user_claim_name(claim_map)
    if not user_claim:
        raise ManagedAuthError(500, "validated_token provider maps no user claim")
    caller = claims.get(user_claim)
    if caller in (None, ""):
        raise ManagedAuthError(401, f'token missing user claim "{user_claim}"')
    subject = _verified_identity_subject(claims)
    issuer = _verified_identity_issuer(provider, claims)
    user: ManagedUser = {
        "identity": str(caller),
        "permissions": [],
        "mda_user_id": str(caller),
        "mda_claims": dict(claims),
        # Provider id + verified iss/sub drive Agent Auth POST /principals.
        "mda_identity_provider": (provider.get("id") or "").strip() or "oidc",
        "mda_identity_issuer": issuer,
        "mda_identity_subject": subject,
    }
    groups = normalize_groups(claims.get(claim_map["groups"])) if claim_map.get("groups") else []
    if groups:
        user["mda_groups"] = groups
    email = claims.get(claim_map["email"]) if claim_map.get("email") else None
    if email:
        user["mda_user_email"] = str(email)
    return user


def _verified_identity_subject(claims: Mapping[str, object]) -> str:
    """OIDC ``sub``, or Supabase introspection ``id`` (same stable value)."""
    raw_sub = claims.get("sub")
    if isinstance(raw_sub, str) and raw_sub.strip():
        return raw_sub.strip()
    raw_id = claims.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    raise ManagedAuthError(401, 'token missing "sub" claim')


def _verified_identity_issuer(
    provider: ValidatedTokenProvider, claims: Mapping[str, object]
) -> str:
    """Token ``iss``, else the provider's configured issuer / first ``issuers`` entry."""
    raw_iss = claims.get("iss")
    if isinstance(raw_iss, str) and raw_iss.strip():
        return raw_iss.strip()
    configured = provider.get("issuer")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    issuers = provider.get("issuers")
    if isinstance(issuers, Sequence) and not isinstance(issuers, (str, bytes)):
        for entry in issuers:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    raise ManagedAuthError(401, 'token missing "iss" claim')


def _verify_provider_claims(
    provider: ValidatedTokenProvider, token: str, headers: Mapping[str, object]
) -> Mapping[str, object]:
    if provider.get("introspect"):
        return _introspect_claims(provider, token, headers)
    return _verify_jwks(provider, token)


def _verify_jwks(provider: ValidatedTokenProvider, token: str) -> Mapping[str, object]:
    audience = provider.get("audience")
    if not audience:
        raise ManagedAuthError(500, "JWKS validated_token provider requires an audience")
    jwt = _load_jwt()
    key = _jwks_signing_key(provider, token)
    algorithms = list(provider.get("algorithms") or ["RS256"])
    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=audience,
            issuer=provider.get("issuer"),
        )
    except Exception as error:
        raise ManagedAuthError(401, "token signature verification failed") from error


def _jwks_signing_key(provider: ValidatedTokenProvider, token: str) -> object:
    jwt = _load_jwt()
    uri = _resolve_jwks_uri(provider)
    client = _jwks_clients.get(uri)
    if client is None:
        client = jwt.PyJWKClient(uri, timeout=_JWKS_HTTP_TIMEOUT_S)
        _jwks_clients[uri] = client
    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except Exception as error:
        # Empty JWKS (common for legacy Supabase HS256 projects) and kid misses
        # must stay 401 — not an unhandled 500 in the ASGI stack.
        raise ManagedAuthError(
            401,
            "token JWKS verification failed (empty key set or unknown kid); "
            "legacy HS256 Supabase projects need auth.supabase(..., introspect=True)",
        ) from error
    return signing_key.key


def _resolve_jwks_uri(provider: ValidatedTokenProvider) -> str:
    if provider.get("jwks"):
        return provider["jwks"]
    issuer = provider.get("issuer")
    if provider.get("discover") and issuer:
        cached = _discover_cache.get(issuer)
        if cached:
            return cached
        base = issuer.rstrip("/")
        config = _http_get_json(f"{base}/.well-known/openid-configuration", {})
        jwks_uri = config.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise ManagedAuthError(401, "OIDC configuration has no jwks_uri")
        _discover_cache[issuer] = jwks_uri
        return jwks_uri
    raise ManagedAuthError(500, "validated_token provider declares neither jwks nor discover")


def _introspect_claims(
    provider: ValidatedTokenProvider, token: str, headers: Mapping[str, object]
) -> Mapping[str, object]:
    introspect = provider["introspect"]
    url, region_headers = _resolve_introspection(introspect, headers)
    # Base headers first, then the selected region's headers so a region can
    # override the shared default (e.g. a per-region Supabase ``apikey``).
    request_headers = {
        key: _expand_env(value) for key, value in (introspect.get("headers") or {}).items()
    }
    for key, value in (region_headers or {}).items():
        request_headers[key] = _expand_env(value)
    if introspect.get("token_in", "authorization") == "authorization":
        request_headers["authorization"] = f"Bearer {token}"
    else:
        request_headers[introspect.get("header", "authorization")] = token
    # Expand `${ENV}` in the endpoint so region URLs can stay deployment-driven
    # (e.g. ``${SUPABASE_EU_URL}/auth/v1/user``) rather than baked in at authoring.
    return _http_get_json(_expand_env(url), request_headers)


def _resolve_introspection(
    introspect: TokenIntrospection, headers: Mapping[str, object]
) -> tuple[str, Mapping[str, str] | None]:
    """Resolve the introspection endpoint and any region-specific headers.

    A single-endpoint provider uses ``introspect.url``; a multi-region provider
    selects by the ``region_header`` value, where each region maps either to a bare
    URL string (shared headers) or a ``{"url", "headers"}`` object carrying its own
    credential (e.g. a per-region Supabase ``apikey``). Fail-closed (401/500).
    """
    if introspect.get("url"):
        return introspect["url"], None
    region_header = introspect.get("region_header")
    regions = introspect.get("regions")
    if region_header and regions:
        region = headers.get(region_header.lower())
        entry = regions.get(region) if region else None
        if not entry:
            raise ManagedAuthError(401, "unknown or missing region for introspection")
        if isinstance(entry, str):
            return entry, None
        return entry["url"], entry.get("headers")
    raise ManagedAuthError(500, "introspection provider declares neither url nor regions")


def _bearer_token(headers: Mapping[str, object], authorization: str | None) -> str:
    raw = authorization or headers.get("authorization") or ""
    scheme, _, token = str(raw).partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token:
        return token
    raise ManagedAuthError(401, "missing bearer token")


def _try_unverified_issuer(token: str) -> str | None:
    if not _looks_like_jwt(token):
        return None
    jwt = _load_jwt()
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception:  # noqa: BLE001 - any decode failure means "not a usable JWT"
        return None
    iss = claims.get("iss")
    return str(iss) if iss is not None else None


_JWT_MIN_DOTS = 2


def _looks_like_jwt(token: str) -> bool:
    return token.count(".") >= _JWT_MIN_DOTS


def _expand_env(value: str) -> str:
    """Expand ``${ENV_VAR}`` references; missing vars fail closed (500)."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None or resolved == "":
            raise ManagedAuthError(500, f"missing required environment variable {name}")
        return resolved

    return re.sub(r"\$\{([A-Z0-9_]+)\}", _replace, value)


def _http_get_json(url: str, headers: Mapping[str, str]) -> Mapping[str, object]:
    request = urllib.request.Request(url, headers=dict(headers))  # noqa: S310 - fixed https IdP URLs
    try:
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=_AUTH_HTTP_TIMEOUT_S
        ) as response:
            return json.loads(response.read().decode())
    except OSError as error:
        # ``URLError`` covers connect/handshake failures, but a socket timeout
        # while the body is still streaming surfaces as a bare ``TimeoutError``.
        # Both mean a hung IdP and must fail closed rather than escape as a 500.
        raise ManagedAuthError(401, "token introspection failed") from error


def _load_jwt() -> PyJWTModule:
    try:
        return cast("PyJWTModule", import_module("jwt"))
    except ModuleNotFoundError as error:  # pragma: no cover - depends on runtime deps
        raise RuntimeError("MDA requires `pyjwt` to verify validated_token ingress.") from error
