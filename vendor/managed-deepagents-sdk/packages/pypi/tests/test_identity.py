"""Unit tests for the identity authoring API."""

from __future__ import annotations

from typing import Any, cast

import pytest

from managed_deepagents import auth, define_identity
from managed_deepagents.identity import (
    IdentityConfig,
    IdentityDefinition,
    validate_identity,
)

BASE: IdentityConfig = {
    "ingress": {"http": "backend"},
    "scope": {"credentials": "user"},
}


def _with(**overrides: Any) -> IdentityConfig:
    return cast("IdentityConfig", {**BASE, **overrides})


class TestDefineIdentity:
    def test_returns_a_tagged_definition(self) -> None:
        identity = define_identity(auth="backend")

        assert isinstance(identity, IdentityDefinition)
        assert identity.kind == "identity"
        assert identity.config["scope"] == {"credentials": "user"}
        # Memory is not an identity axis: declaring identity mounts nothing.
        assert "memory" not in identity.config["scope"]

    def test_rejects_the_legacy_config_keywords(self) -> None:
        with pytest.raises(TypeError, match="scoping"):
            define_identity(scoping=cast("Any", {"memory": "user"}))

    def test_no_longer_exposes_a_preset_helper(self) -> None:
        assert not hasattr(define_identity, "preset")


class TestValidateIdentity:
    def test_rejects_missing_ingress_http(self) -> None:
        with pytest.raises(ValueError, match=r"ingress\.http"):
            validate_identity(cast("IdentityConfig", {"scope": {}}))

    def test_rejects_retired_threads_and_memory_axes(self) -> None:
        with pytest.raises(ValueError, match=r"`scope\.threads` is not supported"):
            validate_identity(_with(scope={"threads": "user"}))
        for memory in ("user", [], [{"kind": "agent"}]):
            with pytest.raises(ValueError, match=r"`scope\.memory` is no longer supported"):
                validate_identity(_with(scope=cast("Any", {"memory": memory})))


class TestValidatedTokenIngress:
    @staticmethod
    def _with_providers(provider_list: list[Any]) -> IdentityDefinition:
        return define_identity(auth=cast("Any", provider_list))

    def test_accepts_single_provider(self) -> None:
        identity = self._with_providers(
            [
                {
                    "issuer": "https://acme.us.auth0.com/",
                    "jwks": "https://acme.us.auth0.com/.well-known/jwks.json",
                    "audience": "api://default",
                    "claims": {"user": "sub"},
                }
            ]
        )
        assert identity.config["ingress"]["http"]["mode"] == "validated_token"

    def test_rejects_empty_providers(self) -> None:
        with pytest.raises(ValueError, match="non-empty `providers`"):
            self._with_providers([])

    def test_rejects_provider_without_user_claim(self) -> None:
        with pytest.raises(ValueError, match=r"requires `claims\.user`"):
            self._with_providers([{"jwks": "https://x/jwks", "claims": {}}])

    def test_requires_ids_for_multiple_providers(self) -> None:
        with pytest.raises(ValueError, match="requires an `id`"):
            self._with_providers(
                [
                    {"jwks": "https://a/jwks", "claims": {"user": "sub"}},
                    {"jwks": "https://b/jwks", "claims": {"user": "sub"}},
                ]
            )

    def test_rejects_provider_without_verifier(self) -> None:
        with pytest.raises(ValueError, match="must declare one of"):
            self._with_providers([{"claims": {"user": "sub"}}])

    def test_requires_audience_for_jwks_verification(self) -> None:
        with pytest.raises(ValueError, match="requires a non-empty `audience`"):
            self._with_providers(
                [
                    {
                        "issuer": "https://acme.us.auth0.com/",
                        "jwks": "https://acme.us.auth0.com/.well-known/jwks.json",
                        "claims": {"user": "sub"},
                    }
                ]
            )

    def test_does_not_require_audience_for_introspection(self) -> None:
        self._with_providers(
            [
                {
                    "introspect": {"url": "https://idp.example/introspect"},
                    "claims": {"user": "sub"},
                }
            ]
        )

    def test_rejects_the_retired_trusted_backend_spelling(self) -> None:
        with pytest.raises(ValueError, match="unknown `auth` value 'trusted_backend'"):
            define_identity(auth=cast("Any", "trusted_backend"))


class TestProviders:
    def test_supabase_local_jwks(self) -> None:
        provider = auth.supabase(project_ref="abc")
        assert provider["jwks"] == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
        assert "introspect" not in provider
        assert provider["audience"] == "authenticated"
        assert provider["claims"]["user"] == "email"

    def test_supabase_url_sets_discovery_url(self) -> None:
        provider = auth.supabase(url="https://auth.langchain.com")
        assert provider["discovery_url"] == "https://auth.langchain.com/auth/v1"
        assert "issuer" not in provider
        assert "jwks" not in provider
        identity = define_identity(auth=cast("Any", provider))
        assert identity.kind == "identity"

    def test_supabase_rejects_project_ref_and_url(self) -> None:
        with pytest.raises(ValueError, match="only one of"):
            auth.supabase(project_ref="abc", url="https://auth.example.com")

    def test_supabase_introspection(self) -> None:
        provider = auth.supabase(
            project_ref="abc",
            introspect=True,
            regions={"us-east-1": "https://us.supabase.co/auth/v1/user"},
        )
        assert provider["introspect"]["headers"] == {"apikey": "${SUPABASE_ANON_KEY}"}
        assert provider["introspect"]["regions"] == {
            "us-east-1": "https://us.supabase.co/auth/v1/user"
        }
        assert provider["introspect"]["region_header"] == "x-supabase-region"
        assert provider["issuers"] == ["https://us.supabase.co/auth/v1"]

    def test_supabase_single_introspect_sets_issuer(self) -> None:
        provider = auth.supabase(project_ref="abc", introspect=True)
        assert provider["issuer"] == "https://abc.supabase.co/auth/v1"
        assert provider["introspect"]["url"] == "https://abc.supabase.co/auth/v1/user"

    def test_supabase_per_region_projects_and_anon_keys(self) -> None:
        provider = auth.supabase(
            introspect=True,
            regions={
                "us": {"project_ref": "us-ref", "anon_key": "${SUPABASE_ANON_KEY}"},
                "eu": {"project_ref": "eu-ref", "anon_key": "${SUPABASE_EU_ANON_KEY}"},
            },
        )
        assert provider["introspect"]["region_header"] == "x-supabase-region"
        assert provider["introspect"]["regions"] == {
            "us": {
                "url": "https://us-ref.supabase.co/auth/v1/user",
                "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
            },
            "eu": {
                "url": "https://eu-ref.supabase.co/auth/v1/user",
                "headers": {"apikey": "${SUPABASE_EU_ANON_KEY}"},
            },
        }
        assert provider["issuers"] == [
            "https://us-ref.supabase.co/auth/v1",
            "https://eu-ref.supabase.co/auth/v1",
        ]

    def test_supabase_custom_region_header(self) -> None:
        provider = auth.supabase(
            introspect=True,
            region_header="x-region",
            regions={"us": {"project_ref": "us-ref"}},
        )
        assert provider["introspect"]["region_header"] == "x-region"
        assert provider["introspect"]["regions"] == {
            "us": {"url": "https://us-ref.supabase.co/auth/v1/user"}
        }

    def test_supabase_requires_project_ref_or_regions(self) -> None:
        with pytest.raises(ValueError, match="project_ref"):
            auth.supabase(introspect=True)
