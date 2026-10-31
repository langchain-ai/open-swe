"""Unit tests for validated_token ingress verification."""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
from typing import Any, ClassVar, Self

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from managed_deepagents import _validated_token
from managed_deepagents._identity_auth import ManagedAuthError
from managed_deepagents._validated_token import (
    claims_to_user,
    resolve_provider,
    resolve_validated_token_user,
    select_provider,
)

AUTH0: dict[str, Any] = {
    "id": "auth0",
    "issuer": "https://acme.auth0.com/",
    "jwks": "https://acme.auth0.com/.well-known/jwks.json",
    "audience": "api://default",
    "claims": {"user": "sub"},
}
CLERK: dict[str, Any] = {
    "id": "clerk",
    "issuer": "https://acme.clerk.accounts.dev",
    "jwks": "https://acme.clerk.accounts.dev/.well-known/jwks.json",
    "audience": "api://default",
    "claims": {"user": "sub"},
}

# 32+ byte keys keep PyJWT's HS256 length warning quiet in tests.
UNVERIFIED_KEY = "unverified-token-key-0123456789abcdef"


def _cfg(providers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ingress": {"http": {"mode": "validated_token", "providers": providers}},
        "scope": {},
    }


class TestSelectProvider:
    def test_single_provider_skips_decoding(self) -> None:
        assert select_provider([AUTH0], "not-a-jwt") is AUTH0

    def test_routes_by_issuer(self) -> None:
        token = jwt.encode({"iss": "https://acme.auth0.com/"}, UNVERIFIED_KEY, algorithm="HS256")
        assert select_provider([CLERK, AUTH0], token) is AUTH0

    def test_rejects_raw_user_id_tokens(self) -> None:
        with pytest.raises(ManagedAuthError, match="malformed token"):
            select_provider([AUTH0, CLERK], "polly-ls-user-123")

    def test_no_match_is_unauthorized(self) -> None:
        token = jwt.encode({"iss": "https://evil/"}, UNVERIFIED_KEY, algorithm="HS256")
        with pytest.raises(
            ManagedAuthError, match=r"no provider matches token issuer 'https://evil/'"
        ):
            select_provider([AUTH0, CLERK], token)

    def test_routes_by_issuers_list(self) -> None:
        multi = {
            "id": "supabase",
            "issuers": [
                "https://us-ref.supabase.co/auth/v1",
                "https://eu-ref.supabase.co/auth/v1",
            ],
            "introspect": {"url": "https://us-ref.supabase.co/auth/v1/user"},
            "claims": {"user": "email"},
        }
        token = jwt.encode(
            {"iss": "https://eu-ref.supabase.co/auth/v1"},
            UNVERIFIED_KEY,
            algorithm="HS256",
        )
        assert select_provider([CLERK, multi], token) is multi


class TestClaimsToUser:
    def test_maps_user_groups_email(self) -> None:
        provider = {
            **AUTH0,
            "claims": {
                "user": "sub",
                "groups": "groups",
                "email": "email",
            },
        }
        user = claims_to_user(
            provider,
            {
                "sub": "u1",
                "org_id": "org9",
                "groups": ["billing", "support"],
                "email": "a@b.com",
                "role": "admin",
            },
        )
        assert user["mda_user_id"] == "u1"
        assert "mda_organization_id" not in user
        assert user["mda_groups"] == ["billing", "support"]
        assert user["mda_user_email"] == "a@b.com"
        assert user["mda_claims"]["role"] == "admin"
        assert user["mda_identity_issuer"] == "https://acme.auth0.com/"
        assert user["mda_identity_subject"] == "u1"

    def test_uses_verified_sub_when_claims_user_is_email(self) -> None:
        provider = {
            **AUTH0,
            "claims": {"user": "email"},
        }
        user = claims_to_user(
            provider,
            {
                "sub": "auth0|u1",
                "iss": "https://acme.auth0.com/",
                "email": "alice@example.com",
            },
        )
        assert user["mda_user_id"] == "alice@example.com"
        assert user["mda_identity_issuer"] == "https://acme.auth0.com/"
        assert user["mda_identity_subject"] == "auth0|u1"

    def test_missing_sub_is_unauthorized(self) -> None:
        provider = {**AUTH0, "claims": {"user": "email"}}
        with pytest.raises(ManagedAuthError, match='"sub"'):
            claims_to_user(
                provider,
                {"email": "alice@example.com", "iss": "https://acme.auth0.com/"},
            )

    def test_ignores_organization_claims(self) -> None:
        provider = {**AUTH0, "claims": {"user": "sub", "organization": "org_id"}}
        user = claims_to_user(provider, {"sub": "u1", "org_id": "org9"})
        assert user["mda_user_id"] == "u1"
        assert "mda_organization_id" not in user

    def test_accepts_delimited_groups_claim(self) -> None:
        provider = {**AUTH0, "claims": {"user": "sub", "groups": "groups"}}
        user = claims_to_user(provider, {"sub": "u1", "groups": "billing support"})
        assert user["mda_groups"] == ["billing", "support"]

    def test_missing_user_is_unauthorized(self) -> None:
        with pytest.raises(ManagedAuthError, match="user claim"):
            claims_to_user(AUTH0, {"org_id": "org9"})


class TestRawBearerTokens:
    """Raw user ids must never be accepted as bearer tokens."""

    def test_rejects_raw_user_id_bearer_tokens(self) -> None:
        for raw in (
            "Bearer polly-ls-user-123",
            "Bearer user-browser-123",
            "Bearer someone@example.com",
        ):
            with pytest.raises(ManagedAuthError, match="malformed token"):
                resolve_validated_token_user(
                    _cfg([AUTH0, CLERK]),
                    _cfg([AUTH0, CLERK])["ingress"]["http"],
                    {},
                    raw,
                )


class TestJwksVerification:
    def test_fails_closed_when_provider_omits_audience(self) -> None:
        provider = {key: value for key, value in AUTH0.items() if key != "audience"}
        with pytest.raises(ManagedAuthError, match="requires an audience"):
            _validated_token._verify_jwks(provider, "token")

    def test_verifies_provider_signed_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(tz=dt.UTC)
        token = jwt.encode(
            {
                "iss": "https://acme.auth0.com/",
                "aud": "api://default",
                "sub": "u1",
                "org_id": "org9",
                "iat": now,
                "exp": now + dt.timedelta(hours=1),
            },
            private_key,
            algorithm="RS256",
        )
        monkeypatch.setattr(
            _validated_token,
            "_jwks_signing_key",
            lambda provider, token: private_key.public_key(),
        )
        provider = {**AUTH0, "audience": "api://default", "algorithms": ["RS256"]}
        user = resolve_validated_token_user(
            _cfg([provider]), _cfg([provider])["ingress"]["http"], {}, f"Bearer {token}"
        )
        assert user["mda_user_id"] == "u1"
        assert "mda_organization_id" not in user

    def test_rejects_wrong_audience(self, monkeypatch: pytest.MonkeyPatch) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(tz=dt.UTC)
        token = jwt.encode(
            {
                "iss": "https://acme.auth0.com/",
                "aud": "someone-else",
                "sub": "u1",
                "iat": now,
                "exp": now + dt.timedelta(hours=1),
            },
            private_key,
            algorithm="RS256",
        )
        monkeypatch.setattr(
            _validated_token,
            "_jwks_signing_key",
            lambda provider, token: private_key.public_key(),
        )
        provider = {**AUTH0, "audience": "api://default", "algorithms": ["RS256"]}
        with pytest.raises(ManagedAuthError, match="verification failed"):
            resolve_validated_token_user(
                _cfg([provider]), _cfg([provider])["ingress"]["http"], {}, f"Bearer {token}"
            )


class TestIntrospection:
    SUPABASE: ClassVar[dict[str, Any]] = {
        "id": "supabase",
        "issuer": "https://us.supabase.co/auth/v1",
        "introspect": {
            "region_header": "x-supabase-region",
            "regions": {"us-east-1": "https://us.supabase.co/auth/v1/user"},
            "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
        },
        "claims": {"user": "email"},
    }

    def test_routes_region_and_expands_apikey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-123")
        calls: dict[str, Any] = {}

        def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
            calls["url"] = url
            calls["headers"] = headers
            return {"id": "user-1", "email": "user@acme.com"}

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)
        user = resolve_validated_token_user(
            _cfg([self.SUPABASE]),
            _cfg([self.SUPABASE])["ingress"]["http"],
            {"x-supabase-region": "us-east-1"},
            "Bearer opaque",
        )
        assert user["mda_user_id"] == "user@acme.com"
        assert calls["url"] == "https://us.supabase.co/auth/v1/user"
        assert calls["headers"]["apikey"] == "anon-123"
        assert calls["headers"]["authorization"] == "Bearer opaque"

    def test_stalled_body_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A socket timeout mid-body is a bare TimeoutError, not a URLError."""

        class StalledResponse:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def read(self) -> bytes:
                raise TimeoutError("timed out")

        monkeypatch.setattr(
            _validated_token.urllib.request,
            "urlopen",
            lambda *_args, **_kwargs: StalledResponse(),
        )
        with pytest.raises(ManagedAuthError, match="introspection failed") as excinfo:
            _validated_token._http_get_json("https://idp.example/auth/v1/user", {})
        assert excinfo.value.status == 401

    def test_unreachable_endpoint_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(_validated_token.urllib.request, "urlopen", refuse)
        with pytest.raises(ManagedAuthError, match="introspection failed") as excinfo:
            _validated_token._http_get_json("https://idp.example/auth/v1/user", {})
        assert excinfo.value.status == 401

    PER_REGION: ClassVar[dict[str, Any]] = {
        "id": "supabase",
        "issuer": "https://us.supabase.co/auth/v1",
        "introspect": {
            "region_header": "x-supabase-region",
            "regions": {
                "us": "https://us.supabase.co/auth/v1/user",
                "eu": {
                    "url": "https://eu.supabase.co/auth/v1/user",
                    "headers": {"apikey": "${SUPABASE_EU_ANON_KEY}"},
                },
            },
            "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
        },
        "claims": {"user": "email"},
    }

    def test_per_region_anon_key_overrides_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_ANON_KEY", "base-anon")
        monkeypatch.setenv("SUPABASE_EU_ANON_KEY", "eu-anon")
        calls: dict[str, Any] = {}

        def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
            calls["url"] = url
            calls["headers"] = headers
            return {"id": "user-1", "email": "eu@acme.com"}

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)
        user = resolve_validated_token_user(
            _cfg([self.PER_REGION]),
            _cfg([self.PER_REGION])["ingress"]["http"],
            {"x-supabase-region": "eu"},
            "Bearer opaque",
        )
        assert user["mda_user_id"] == "eu@acme.com"
        assert calls["url"] == "https://eu.supabase.co/auth/v1/user"
        assert calls["headers"]["apikey"] == "eu-anon"

    def test_bare_string_region_uses_base_apikey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_ANON_KEY", "base-anon")
        calls: dict[str, Any] = {}

        def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
            calls["url"] = url
            calls["headers"] = headers
            return {"id": "user-1", "email": "us@acme.com"}

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)
        resolve_validated_token_user(
            _cfg([self.PER_REGION]),
            _cfg([self.PER_REGION])["ingress"]["http"],
            {"x-supabase-region": "us"},
            "Bearer opaque",
        )
        assert calls["url"] == "https://us.supabase.co/auth/v1/user"
        assert calls["headers"]["apikey"] == "base-anon"

    def test_region_url_expands_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-123")
        monkeypatch.setenv("SUPABASE_EU_URL", "https://eu.supabase.co")
        provider: dict[str, Any] = {
            "id": "supabase",
            "issuer": "https://eu.supabase.co/auth/v1",
            "introspect": {
                "region_header": "x-supabase-region",
                "regions": {"eu": "${SUPABASE_EU_URL}/auth/v1/user"},
                "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
            },
            "claims": {"user": "email"},
        }
        calls: dict[str, Any] = {}

        def fake_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
            calls["url"] = url
            return {"id": "user-1", "email": "eu@acme.com"}

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)
        resolve_validated_token_user(
            _cfg([provider]),
            _cfg([provider])["ingress"]["http"],
            {"x-supabase-region": "eu"},
            "Bearer opaque",
        )
        assert calls["url"] == "https://eu.supabase.co/auth/v1/user"

    def test_unknown_region_is_unauthorized(self) -> None:
        with pytest.raises(ManagedAuthError, match="region"):
            resolve_validated_token_user(
                _cfg([self.SUPABASE]),
                _cfg([self.SUPABASE])["ingress"]["http"],
                {"x-supabase-region": "moon-1"},
                "Bearer opaque",
            )


def test_missing_bearer_is_unauthorized() -> None:
    with pytest.raises(ManagedAuthError, match="bearer"):
        resolve_validated_token_user(_cfg([AUTH0]), _cfg([AUTH0])["ingress"]["http"], {}, None)


class TestMaterializeDiscovery:
    def setup_method(self) -> None:
        _validated_token._discover_cache.clear()
        _validated_token._oidc_issuer_cache.clear()
        _validated_token._jwks_clients.clear()

    def test_custom_domain_discovers_real_issuer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url: str, _headers: dict[str, str]) -> dict[str, str]:
            assert url.endswith("/.well-known/openid-configuration")
            assert "auth.langchain.com" in url
            return {
                "issuer": "https://projref.supabase.co/auth/v1",
                "jwks_uri": "https://projref.supabase.co/auth/v1/.well-known/jwks.json",
            }

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)
        provider = {
            "id": "supabase",
            "discovery_url": "https://auth.langchain.com/auth/v1",
            "algorithms": ["ES256", "RS256"],
            "claims": {"user": "email"},
        }
        token = jwt.encode(
            {"sub": "u1", "iss": "https://projref.supabase.co/auth/v1"},
            "test-signing-key-at-least-32-bytes!!",
            algorithm="HS256",
        )
        materialized = resolve_provider([provider], token)
        assert materialized["issuer"] == "https://projref.supabase.co/auth/v1"
        assert materialized["jwks"] == "https://projref.supabase.co/auth/v1/.well-known/jwks.json"

    def test_empty_jwks_is_unauthorized_not_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class EmptyJwksClient:
            def __init__(self, _uri: str, *, timeout: int) -> None:
                self.timeout = timeout

            def get_signing_key_from_jwt(self, _token: str) -> object:
                raise RuntimeError("The JWK Set did not contain any keys")

        class FakeJwtModule:
            PyJWKClient = EmptyJwksClient

        monkeypatch.setattr(
            _validated_token,
            "_resolve_jwks_uri",
            lambda _provider: "https://projref.supabase.co/auth/v1/.well-known/jwks.json",
        )
        monkeypatch.setattr(_validated_token, "_load_jwt", FakeJwtModule)
        provider = {
            "id": "supabase",
            "issuer": "https://projref.supabase.co/auth/v1",
            "jwks": "https://projref.supabase.co/auth/v1/.well-known/jwks.json",
            "claims": {"user": "email"},
        }
        with pytest.raises(ManagedAuthError, match=r"empty key set|introspect"):
            _validated_token._jwks_signing_key(provider, "header.payload.sig")

    def test_jwks_client_is_built_with_a_bounded_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[int] = []

        class RecordingJwksClient:
            def __init__(self, _uri: str, *, timeout: int) -> None:
                built.append(timeout)

            def get_signing_key_from_jwt(self, _token: str) -> object:
                raise RuntimeError("The JWK Set did not contain any keys")

        class FakeJwtModule:
            PyJWKClient = RecordingJwksClient

        monkeypatch.setattr(
            _validated_token,
            "_resolve_jwks_uri",
            lambda _provider: "https://projref.supabase.co/auth/v1/.well-known/jwks.json",
        )
        monkeypatch.setattr(_validated_token, "_load_jwt", FakeJwtModule)
        provider = {
            "id": "supabase",
            "issuer": "https://projref.supabase.co/auth/v1",
            "jwks": "https://projref.supabase.co/auth/v1/.well-known/jwks.json",
            "claims": {"user": "email"},
        }
        with pytest.raises(ManagedAuthError):
            _validated_token._jwks_signing_key(provider, "header.payload.sig")

        assert built == [_validated_token._JWKS_HTTP_TIMEOUT_S]

    def test_resolve_routes_custom_domain_provider_by_discovered_iss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_get(url: str, _headers: dict[str, str]) -> dict[str, object]:
            if url.endswith("/openid-configuration"):
                return {
                    "issuer": "https://projref.supabase.co/auth/v1",
                    "jwks_uri": "https://projref.supabase.co/auth/v1/.well-known/jwks.json",
                }
            raise AssertionError(f"unexpected url {url}")

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)

        # Bypass JWKS crypto: after selection, stub verification.
        monkeypatch.setattr(
            _validated_token,
            "_verify_provider_claims",
            lambda provider, token, headers: {
                "iss": "https://projref.supabase.co/auth/v1",
                "sub": "u1",
                "email": "ada@acme.com",
            },
        )

        provider = {
            "id": "supabase",
            "discovery_url": "https://auth.langchain.com/auth/v1",
            "algorithms": ["ES256"],
            "claims": {"user": "email"},
        }
        token = jwt.encode(
            {
                "iss": "https://projref.supabase.co/auth/v1",
                "sub": "u1",
                "email": "ada@acme.com",
            },
            UNVERIFIED_KEY,
            algorithm="HS256",
        )
        user = resolve_validated_token_user(
            _cfg([provider, CLERK]),
            _cfg([provider, CLERK])["ingress"]["http"],
            {},
            f"Bearer {token}",
        )
        assert user["mda_user_id"] == "ada@acme.com"

    def test_known_issuer_skips_unrelated_discovery_outage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(url: str, _headers: dict[str, str]) -> dict[str, object]:
            raise AssertionError(f"discovery should not run for a known issuer: {url}")

        monkeypatch.setattr(_validated_token, "_http_get_json", boom)
        provider = {
            "id": "supabase",
            "discovery_url": "https://auth.langchain.com/auth/v1",
            "algorithms": ["ES256"],
            "claims": {"user": "email"},
        }
        token = jwt.encode(
            {"iss": "https://acme.clerk.accounts.dev", "sub": "u1"},
            UNVERIFIED_KEY,
            algorithm="HS256",
        )
        assert resolve_provider([provider, CLERK], token) is CLERK

    def test_malformed_discovery_does_not_block_other_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_get(url: str, _headers: dict[str, str]) -> object:
            if "broken.example" in url:
                raise json.JSONDecodeError("Expecting value", "not-json", 0)
            if url.endswith("/openid-configuration"):
                return {
                    "issuer": "https://good.supabase.co/auth/v1",
                    "jwks_uri": "https://good.supabase.co/auth/v1/.well-known/jwks.json",
                }
            raise AssertionError(f"unexpected url {url}")

        monkeypatch.setattr(_validated_token, "_http_get_json", fake_get)
        monkeypatch.setattr(
            _validated_token,
            "_verify_provider_claims",
            lambda provider, token, headers: {
                "iss": "https://good.supabase.co/auth/v1",
                "sub": "u1",
                "email": "ada@acme.com",
            },
        )

        broken = {
            "id": "broken",
            "discovery_url": "https://broken.example/auth/v1",
            "algorithms": ["ES256"],
            "claims": {"user": "email"},
        }
        good = {
            "id": "good",
            "discovery_url": "https://good.example/auth/v1",
            "algorithms": ["ES256"],
            "claims": {"user": "email"},
        }
        token = jwt.encode(
            {
                "iss": "https://good.supabase.co/auth/v1",
                "sub": "u1",
                "email": "ada@acme.com",
            },
            UNVERIFIED_KEY,
            algorithm="HS256",
        )
        user = resolve_validated_token_user(
            _cfg([broken, good]),
            _cfg([broken, good])["ingress"]["http"],
            {},
            f"Bearer {token}",
        )
        assert user["mda_user_id"] == "ada@acme.com"
