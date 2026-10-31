"""Tests for Trigger identity_context JWT verification."""

from __future__ import annotations

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from managed_deepagents._channels.trigger import identity_context
from managed_deepagents._channels.trigger.identity_context import (
    IdentityContextUnavailableError,
    InvalidIdentityContextError,
    optional_principal_id_from_identity_context,
    principal_id_from_identity_context,
)

PRINCIPAL_ID = "11111111-1111-4111-8111-111111111111"
ISSUER = "https://api.smith.langchain.com"

_SIGNING_KEY = Ed25519PrivateKey.generate()


def test_configured_issuer_overrides_internal_api_endpoint() -> None:
    env = {
        "OAUTH_AS_ISSUER": " https://self-hosted.example/langsmith/api/ ",
        "LANGSMITH_ENDPOINT": "http://internal/api/v1",
    }
    assert (
        identity_context.resolve_identity_context_issuer(env)
        == "https://self-hosted.example/langsmith/api"
    )
    assert (
        identity_context.resolve_identity_context_jwks_url(env)
        == "https://self-hosted.example/langsmith/api/.well-known/jwks.json"
    )


def test_issuer_endpoint_fallbacks_are_preserved() -> None:
    assert (
        identity_context.resolve_identity_context_issuer(
            {"OAUTH_AS_ISSUER": " ", "LANGSMITH_ENDPOINT": "https://api.example/"}
        )
        == "https://api.example"
    )
    assert (
        identity_context.resolve_identity_context_issuer(
            {"LANGCHAIN_ENDPOINT": "https://legacy.example/"}
        )
        == "https://legacy.example"
    )
    assert identity_context.resolve_identity_context_issuer({}) == ISSUER


def test_signed_token_with_wrong_configured_issuer_is_rejected() -> None:
    with pytest.raises(InvalidIdentityContextError):
        principal_id_from_identity_context(
            mint_identity_context(),
            key=_SIGNING_KEY.public_key(),
            env={"OAUTH_AS_ISSUER": "https://other.example/api"},
        )


def _verify_kwargs(key: object | None = None) -> dict[str, object]:
    return {"key": key or _SIGNING_KEY.public_key(), "issuer": ISSUER}


def mint_identity_context(
    *,
    claims: dict[str, object] | None = None,
    typ: str = "identity-context+jwt",
    subject: str | None = None,
    audience: object = "mda",
    exp: float | None = None,
    include_exp: bool = True,
    signing_key: Ed25519PrivateKey | rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | None = None,
    algorithm: str = "EdDSA",
) -> str:
    payload: dict[str, object] = {
        "version": "1",
        "tenant_id": "22222222-2222-4222-8222-222222222222",
        "principal": {"id": PRINCIPAL_ID, "kind": "person"},
        "agent": {"id": "33333333-3333-4333-8333-333333333333"},
        "iss": ISSUER,
        "sub": subject or PRINCIPAL_ID,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(exp if exp is not None else time.time() + 300),
    }
    if claims:
        payload.update(claims)
    if not include_exp:
        payload.pop("exp", None)
    return jwt.encode(
        payload,
        signing_key or _SIGNING_KEY,
        algorithm=algorithm,
        headers={"typ": typ},
    )


def mint_unsigned_identity_context() -> str:
    def _b64url(data: dict[str, object]) -> str:
        raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    header = {"alg": "none", "typ": "identity-context+jwt"}
    payload = {
        "version": "1",
        "iss": ISSUER,
        "sub": PRINCIPAL_ID,
        "aud": "mda",
        "exp": int(time.time() + 300),
        "principal": {"id": PRINCIPAL_ID, "kind": "person"},
    }
    return f"{_b64url(header)}.{_b64url(payload)}."


def test_principal_id_from_valid_identity_context() -> None:
    token = mint_identity_context()
    assert principal_id_from_identity_context(token, **_verify_kwargs()) == PRINCIPAL_ID


def test_accepts_rs256_token_under_rsa_key() -> None:
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = mint_identity_context(signing_key=rsa_key, algorithm="RS256")
    assert (
        principal_id_from_identity_context(token, **_verify_kwargs(rsa_key.public_key()))
        == PRINCIPAL_ID
    )


@pytest.mark.parametrize("algorithm", ["PS256", "ES256"])
def test_accepts_ps256_and_es256_tokens(algorithm: str) -> None:
    key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if algorithm == "PS256"
        else ec.generate_private_key(ec.SECP256R1())
    )
    token = mint_identity_context(signing_key=key, algorithm=algorithm)
    assert (
        principal_id_from_identity_context(token, **_verify_kwargs(key.public_key()))
        == PRINCIPAL_ID
    )


def test_rejects_es256_header_under_rsa_key() -> None:
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_jwk = jwt.PyJWK(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key())))
    token = mint_identity_context(
        signing_key=ec.generate_private_key(ec.SECP256R1()), algorithm="ES256"
    )
    with pytest.raises(InvalidIdentityContextError):
        principal_id_from_identity_context(token, **_verify_kwargs(rsa_jwk))


def test_rejects_hs256_token() -> None:
    token = jwt.encode(
        {"iss": ISSUER, "aud": "mda", "exp": int(time.time() + 300)},
        "k" * 32,
        algorithm="HS256",
        headers={"typ": "identity-context+jwt"},
    )
    with pytest.raises(InvalidIdentityContextError, match="alg must be"):
        principal_id_from_identity_context(token, **_verify_kwargs())


def test_rejects_alg_that_does_not_match_key_type() -> None:
    # PyJWK is what the JWKS path selects by kid; PyJWT binds the alg to it.
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_jwk = jwt.PyJWK(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key())))
    ed_jwk = jwt.PyJWK(json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(_SIGNING_KEY.public_key())))
    with pytest.raises(InvalidIdentityContextError):
        principal_id_from_identity_context(mint_identity_context(), **_verify_kwargs(rsa_jwk))
    rs256_token = mint_identity_context(signing_key=rsa_key, algorithm="RS256")
    with pytest.raises(InvalidIdentityContextError):
        principal_id_from_identity_context(rs256_token, **_verify_kwargs(ed_jwk))


def test_rejects_wrong_typ() -> None:
    with pytest.raises(InvalidIdentityContextError):
        principal_id_from_identity_context(mint_identity_context(typ="JWT"), **_verify_kwargs())


def test_rejects_unsigned_token() -> None:
    with pytest.raises(InvalidIdentityContextError, match="alg must be an asymmetric"):
        principal_id_from_identity_context(mint_unsigned_identity_context(), **_verify_kwargs())


def test_rejects_untrusted_signing_key() -> None:
    token = mint_identity_context(signing_key=Ed25519PrivateKey.generate())
    with pytest.raises(InvalidIdentityContextError, match="signature verification"):
        principal_id_from_identity_context(token, **_verify_kwargs())


def test_rejects_expired_token() -> None:
    token = mint_identity_context(exp=time.time() - 10)
    with pytest.raises(InvalidIdentityContextError, match="expired"):
        principal_id_from_identity_context(token, **_verify_kwargs())


def test_rejects_principal_sub_disagreement() -> None:
    token = mint_identity_context(subject="44444444-4444-4444-8444-444444444444")
    with pytest.raises(InvalidIdentityContextError, match="disagree"):
        principal_id_from_identity_context(token, **_verify_kwargs())


def test_rejects_token_without_exp() -> None:
    token = mint_identity_context(include_exp=False)
    with pytest.raises(InvalidIdentityContextError, match="missing exp"):
        principal_id_from_identity_context(token, **_verify_kwargs())


def test_jwks_timeouts_are_unavailable() -> None:
    token = mint_identity_context()

    class _TimeoutJwks:
        def get_signing_key_from_jwt(self, _token: str) -> object:
            raise TimeoutError("JWKS timed out")

    identity_context._jwks_clients["https://api.smith.langchain.com/.well-known/jwks.json"] = (
        _TimeoutJwks()
    )  # type: ignore[assignment]
    try:
        with pytest.raises(IdentityContextUnavailableError):
            principal_id_from_identity_context(token, issuer=ISSUER)
    finally:
        identity_context._jwks_clients.clear()


def test_optional_blank_tokens_are_absent() -> None:
    assert optional_principal_id_from_identity_context(None) is None
    assert optional_principal_id_from_identity_context("") is None
    assert optional_principal_id_from_identity_context("   ") is None
