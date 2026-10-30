"""Simplified ``define_identity(...)`` options and their normalization.

Mirrors ``packages/npm/src/identity/options.test.ts``.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

from managed_deepagents.identity import auth, define_identity

_LANGSMITH_API_KEY_CONFIG = {
    "ingress": {"http": {"mode": "langsmith_api_key"}},
    "scope": {"credentials": "user"},
}


def test_uses_explicit_langsmith_api_key_auth_with_per_caller_credentials() -> None:
    assert define_identity(auth=auth.langsmith_api_key()).config == _LANGSMITH_API_KEY_CONFIG


def test_exposes_only_the_auth_and_resolve_user_keywords() -> None:
    assert list(inspect.signature(define_identity).parameters) == ["auth", "resolve_user"]


def test_keeps_resolve_user_as_the_channels_default() -> None:
    def resolve_user(_request: object) -> None:
        return None

    assert define_identity(auth="backend", resolve_user=resolve_user).resolve_user is resolve_user
    with pytest.raises(TypeError, match="resolve_user must be callable or an endpoint"):
        define_identity(auth="backend", resolve_user=42)  # type: ignore[arg-type]


def test_requires_an_auth_declaration() -> None:
    assert inspect.signature(define_identity).parameters["auth"].default is inspect.Parameter.empty


def test_accepts_an_explicit_backend() -> None:
    assert define_identity(auth="backend").config["ingress"]["http"] == "backend"


def test_accepts_explicit_langsmith_api_key_auth() -> None:
    assert define_identity(auth=auth.langsmith_api_key()).config["ingress"]["http"] == {
        "mode": "langsmith_api_key"
    }


def test_rejects_the_retired_trusted_backend_spelling() -> None:
    with pytest.raises(ValueError, match="unknown `auth` value 'trusted_backend'"):
        define_identity(auth=cast("Any", "trusted_backend"))


def test_lifts_a_single_provider_into_validated_token_ingress() -> None:
    http = define_identity(auth=auth.supabase(project_ref="abcdefgh")).config["ingress"]["http"]

    assert isinstance(http, dict)
    assert http["mode"] == "validated_token"
    assert len(http["providers"]) == 1


def test_rejects_an_unknown_auth_string() -> None:
    with pytest.raises(ValueError, match="unknown `auth` value 'browser'"):
        define_identity(auth=cast("Any", "browser"))


@pytest.mark.parametrize("keyword", ["scope", "credentials", "connect"])
def test_rejects_removed_keywords(keyword: str) -> None:
    with pytest.raises(TypeError, match=f"unexpected keyword argument '{keyword}'"):
        define_identity(**cast("Any", {keyword: {}}))


def test_returns_fresh_scope_data() -> None:
    first = define_identity(auth=auth.langsmith_api_key()).config
    second = define_identity(auth=auth.langsmith_api_key()).config

    first["scope"].clear()

    assert second == _LANGSMITH_API_KEY_CONFIG
