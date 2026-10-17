"""Unit tests for the generated managed custom-auth handler."""

from __future__ import annotations

import asyncio
from ssl import SSLCertVerificationError
from types import SimpleNamespace
from typing import Any, ClassVar, Self, cast
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest

import pytest
from langgraph_sdk import Auth
from langgraph_sdk.auth.exceptions import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException

from managed_deepagents import _identity_auth, channels
from managed_deepagents._channel import collect_channels
from managed_deepagents._channels import source_providers
from managed_deepagents._identity_auth import (
    CHANNEL_EVENTS_USER,
    ManagedAuthError,
    _assert_store_access,
    _deny_unless_assistant_read,
    _normalize_headers,
    _resolve_langsmith_api_key_user_sync,
    _resolve_trusted_backend_user,
    _thread_owner_key,
    build_managed_auth,
    channel_events_bypass_user,
    is_channel_events_request,
    resolve_request_identity,
)
from managed_deepagents.identity import define_identity

SECRET = "s3cret-ingress-token"


class _LangSmithResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


@pytest.fixture(autouse=True)
def _clear_langsmith_verification_cache() -> None:
    _identity_auth.clear_langsmith_verification_cache_for_tests()


class TestResolveLangSmithApiKeyUser:
    @pytest.fixture(autouse=True)
    def _configure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            _identity_auth.LANGSMITH_AUTH_ENDPOINT_ENV,
            "https://auth.example/api/v1",
        )
        monkeypatch.setenv(_identity_auth.LANGSMITH_TENANT_ID_ENV, "tenant-123")

    def test_verifies_key_and_returns_namespaced_service_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_urls: list[str] = []

        def open_auth(request: UrlRequest, *, timeout: float) -> _LangSmithResponse:
            requested_urls.append(request.full_url)
            assert timeout == 2.0
            return _LangSmithResponse(
                b'{"tenant_id":"tenant-123","user_id":"user-456","user_email":"dev@example.com"}'
            )

        monkeypatch.setattr(_identity_auth, "urlopen", open_auth)

        assert _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"}) == {
            "identity": "langsmith:user:user-456",
            "permissions": [],
            "mda_user_id": "langsmith:user:user-456",
            "mda_user_kind": "service",
            "mda_user_email": "dev@example.com",
        }
        assert requested_urls == ["https://auth.example/api/v1/auth/public"]

    def test_async_boundary_delegates_to_the_blocking_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected: _identity_auth.ManagedUser = {
            "identity": "langsmith:tenant:tenant-123",
            "permissions": [],
            "mda_user_id": "langsmith:tenant:tenant-123",
            "mda_user_kind": "service",
        }
        monkeypatch.setattr(
            _identity_auth,
            "_resolve_langsmith_api_key_user_sync",
            lambda _headers: expected,
        )

        actual = asyncio.run(_identity_auth._resolve_langsmith_api_key_user({"x-api-key": "key"}))

        assert actual == expected

    def test_falls_back_to_tenant_principal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _identity_auth,
            "urlopen",
            lambda *_args, **_kwargs: _LangSmithResponse(b'{"tenant_id":"tenant-123"}'),
        )

        user = _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        assert user["mda_user_id"] == "langsmith:tenant:tenant-123"

    def test_rejects_key_from_another_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            _identity_auth,
            "urlopen",
            lambda *_args, **_kwargs: _LangSmithResponse(b'{"tenant_id":"tenant-other"}'),
        )

        with pytest.raises(ManagedAuthError, match="tenant mismatch"):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})

    @pytest.mark.parametrize(
        "failure", [TimeoutError(), URLError(OSError()), 408, 500, 502, 503, 504]
    )
    def test_retries_transient_failure(
        self, monkeypatch: pytest.MonkeyPatch, failure: object
    ) -> None:
        error = (
            HTTPError("https://auth.example", failure, "unavailable", None, None)
            if isinstance(failure, int)
            else failure
        )
        network = Mock(side_effect=[error, _LangSmithResponse(b'{"tenant_id":"tenant-123"}')])
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        monkeypatch.setattr(_identity_auth.secrets, "randbelow", lambda upper: upper - 1)

        assert (
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})["identity"]
            == "langsmith:tenant:tenant-123"
        )
        assert network.call_count == 2
        sleep.assert_called_once_with(0.1)

    @pytest.mark.parametrize("error", [TimeoutError(), ConnectionResetError()])
    def test_retries_response_body_failure(
        self, monkeypatch: pytest.MonkeyPatch, error: OSError
    ) -> None:
        class FailingResponse(_LangSmithResponse):
            def read(self) -> bytes:
                raise error

        network = Mock(
            side_effect=[FailingResponse(b""), _LangSmithResponse(b'{"tenant_id":"tenant-123"}')]
        )
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        monkeypatch.setattr(_identity_auth.secrets, "randbelow", lambda upper: upper - 1)

        assert (
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})["identity"]
            == "langsmith:tenant:tenant-123"
        )
        assert network.call_count == 2
        sleep.assert_called_once_with(0.1)

    @pytest.mark.parametrize("failure", [TimeoutError(), 503])
    def test_retry_exhaustion(self, monkeypatch: pytest.MonkeyPatch, failure: object) -> None:
        error = (
            HTTPError("https://auth.example", failure, "unavailable", None, None)
            if isinstance(failure, int)
            else failure
        )
        network = Mock(side_effect=error)
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        monkeypatch.setattr(_identity_auth.secrets, "randbelow", lambda upper: upper - 1)

        with pytest.raises(ManagedAuthError, match="LangSmith API-key verification unavailable"):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        assert network.call_count == 3
        assert [call.args[0] for call in sleep.call_args_list] == [0.1, 0.2]

    @pytest.mark.parametrize(
        ("status", "message"),
        [
            (401, "invalid API key"),
            (403, "API key is forbidden"),
            (400, "verification unavailable"),
            (429, "verification unavailable"),
        ],
    )
    def test_does_not_retry_http_rejection(
        self, monkeypatch: pytest.MonkeyPatch, status: int, message: str
    ) -> None:
        network = Mock(
            side_effect=HTTPError("https://auth.example", status, "rejected", None, None)
        )
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        with pytest.raises(ManagedAuthError, match=message):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        network.assert_called_once()
        sleep.assert_not_called()

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            (b"not-json", "invalid response"),
            (b"[]", "invalid response"),
            (b'{"tenant_id":"other"}', "tenant mismatch"),
        ],
    )
    def test_does_not_retry_invalid_identity(
        self, monkeypatch: pytest.MonkeyPatch, payload: bytes, message: str
    ) -> None:
        network = Mock(return_value=_LangSmithResponse(payload))
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        with pytest.raises(ManagedAuthError, match=message):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        network.assert_called_once()
        sleep.assert_not_called()

    @pytest.mark.parametrize("reason", [SSLCertVerificationError(), "unknown url type"])
    def test_does_not_retry_configuration_errors(
        self, monkeypatch: pytest.MonkeyPatch, reason: object
    ) -> None:
        network = Mock(side_effect=URLError(reason))
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        with pytest.raises(ManagedAuthError, match="verification unavailable"):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        network.assert_called_once()
        sleep.assert_not_called()

    @pytest.mark.parametrize(
        "endpoint",
        ["ftp://auth.example", "file:///tmp/auth", "https://user:pass@auth.example"],
    )
    def test_rejects_unsupported_endpoint_without_request(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str
    ) -> None:
        monkeypatch.setenv(_identity_auth.LANGSMITH_AUTH_ENDPOINT_ENV, endpoint)
        network = Mock()
        sleep = Mock()
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "sleep", sleep)
        with pytest.raises(ManagedAuthError, match="verification unavailable"):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        network.assert_not_called()
        sleep.assert_not_called()

    def test_reuses_verified_key_for_sixty_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        network = Mock(return_value=_LangSmithResponse(b'{"tenant_id":"tenant-123"}'))
        now = Mock(return_value=1000.0)
        monkeypatch.setattr(_identity_auth, "urlopen", network)
        monkeypatch.setattr(_identity_auth.time, "monotonic", now)

        _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        now.return_value = 1059.0
        _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        assert network.call_count == 1

        _resolve_langsmith_api_key_user_sync({"x-api-key": "other-key"})
        _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key", "x-tenant-id": "tenant-123"})
        assert network.call_count == 3

        now.return_value = 1060.0
        _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        assert network.call_count == 4

    def test_does_not_cache_failed_verification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        network = Mock(
            side_effect=[
                HTTPError("https://auth.example", 401, "rejected", None, None),
                _LangSmithResponse(b'{"tenant_id":"tenant-123"}'),
            ]
        )
        monkeypatch.setattr(_identity_auth, "urlopen", network)

        with pytest.raises(ManagedAuthError, match="invalid API key"):
            _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        _resolve_langsmith_api_key_user_sync({"x-api-key": "test-key"})
        assert network.call_count == 2

    def test_missing_key_is_rejected_without_exposing_configuration(self) -> None:
        with pytest.raises(ManagedAuthError, match="missing x-api-key"):
            _resolve_langsmith_api_key_user_sync({})


def _cfg(**scope_overrides: object) -> dict[str, Any]:
    return {
        "ingress": {"http": "backend"},
        "scope": {**scope_overrides},
    }


def _headers(**overrides: str) -> dict[str, str]:
    base = {
        "x-mda-ingress-secret": SECRET,
        "x-mda-user-id": "user-123",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _set_ingress_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_identity_auth.INGRESS_SECRET_ENV, SECRET)


class TestResolveTrustedBackendUser:
    def test_ignores_groups_header(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(
            cfg,
            {
                "x-mda-ingress-secret": SECRET,
                "x-mda-user-id": "user-123",
                "x-mda-groups": "admins",
            },
        )
        assert user == {
            "identity": "user-123",
            "permissions": [],
            "mda_user_id": "user-123",
        }

    def test_ignores_organization_header(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(cfg, _headers(**{"x-mda-organization-id": "org-9"}))
        assert "mda_organization_id" not in user

    def test_rejects_missing_ingress_secret(self) -> None:
        cfg = _cfg()
        headers = _headers()
        del headers["x-mda-ingress-secret"]
        with pytest.raises(ManagedAuthError) as exc:
            _resolve_trusted_backend_user(cfg, headers)
        assert exc.value.status == 401

    def test_rejects_wrong_ingress_secret(self) -> None:
        cfg = _cfg()
        with pytest.raises(ManagedAuthError):
            _resolve_trusted_backend_user(cfg, _headers(**{"x-mda-ingress-secret": "wrong"}))

    def test_rejects_missing_user(self) -> None:
        cfg = _cfg()
        headers = _headers()
        del headers["x-mda-user-id"]
        with pytest.raises(ManagedAuthError, match="x-mda-user-id"):
            _resolve_trusted_backend_user(cfg, headers)

    def test_prefers_current_spelling_when_both_present(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(cfg, _headers(**{"x-mda-user-id": "user-1"}))
        assert user["mda_user_id"] == "user-1"

    def test_falls_back_to_local_dev_actor_without_ingress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_identity_auth.LOCAL_DEV_ENV, "1")
        monkeypatch.delenv(_identity_auth.INGRESS_SECRET_ENV, raising=False)
        cfg = _cfg()
        assert _resolve_trusted_backend_user(cfg, {}) == {
            "identity": "mda:local-dev",
            "permissions": [],
            "mda_user_id": "mda:local-dev",
            "mda_user_kind": "service",
        }

    def test_prefers_real_ingress_under_local_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_identity_auth.LOCAL_DEV_ENV, "1")
        cfg = _cfg()
        assert _resolve_trusted_backend_user(cfg, _headers()) == {
            "identity": "user-123",
            "permissions": [],
            "mda_user_id": "user-123",
        }

    def test_stamps_service_kind_for_slack_agent_principal(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(
            cfg,
            _headers(**{"x-mda-user-id": "mda:slack"}),
        )
        assert user["mda_user_id"] == "mda:slack"
        assert user["mda_user_kind"] == "service"

    def test_stamps_service_kind_for_channel_principal(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(
            cfg,
            _headers(**{"x-mda-user-id": "mda:channel"}),
        )
        assert user["mda_user_id"] == "mda:channel"
        assert user["mda_user_kind"] == "service"

    def test_slack_source_provider_does_not_force_service_kind(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(
            cfg,
            _headers(
                **{
                    "x-mda-source-provider": "slack",
                    "x-mda-source-thread-id": "slack:v1:key",
                }
            ),
        )
        assert user["mda_source_provider"] == "slack"
        assert user["mda_source_thread_id"] == "slack:v1:key"
        assert "mda_user_kind" not in user
        assert "mda_agent_auth_principal_id" not in user

    def test_uuid_shaped_user_id_is_not_an_agent_auth_principal(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(
            cfg,
            _headers(
                **{
                    "x-mda-user-id": "11111111-1111-4111-8111-111111111111",
                    "x-mda-source-provider": "slack",
                    "x-mda-source-thread-id": "slack:v1:key",
                }
            ),
        )
        assert user["mda_user_id"] == "11111111-1111-4111-8111-111111111111"
        assert "mda_agent_auth_principal_id" not in user

    def test_stamps_agent_auth_principal_from_trigger_handoff_header(self) -> None:
        cfg = _cfg()
        user = _resolve_trusted_backend_user(
            cfg,
            _headers(
                **{
                    "x-mda-user-id": "11111111-1111-4111-8111-111111111111",
                    "x-mda-agent-auth-principal-id": "11111111-1111-4111-8111-111111111111",
                    "x-mda-source-provider": "slack",
                    "x-mda-source-thread-id": "slack:v1:key",
                }
            ),
        )
        assert user["mda_agent_auth_principal_id"] == "11111111-1111-4111-8111-111111111111"


class TestThreadOwnerKey:
    CONVERSATION: ClassVar[dict[str, Any]] = {
        "mda_source_provider": "slack",
        "mda_source_thread_id": "slack:team:chan",
    }

    def test_uses_caller_user_id(self) -> None:
        assert _thread_owner_key({"mda_user_id": "user-1"}) == "user-1"

    def test_owns_channel_threads_by_source_conversation(self) -> None:
        user = {"mda_user_id": "user-1", **self.CONVERSATION}
        assert _thread_owner_key(user) == "mda:source-thread:slack:slack:team:chan"

    def test_every_principal_in_one_conversation_shares_an_owner(self) -> None:
        # A Trigger delivery carries whichever principal ``identity_context``
        # resolved to, and envelopes for the same Slack thread disagree:
        # interrupt resumes arrive without one, and any participant may answer.
        # Keying on the principal 404s the resume against the thread the first
        # message created.
        assert _thread_owner_key({"mda_user_id": "principal-a", **self.CONVERSATION}) == (
            _thread_owner_key({"mda_user_id": "mda:channel", **self.CONVERSATION})
        )

    def test_keeps_separate_conversations_apart(self) -> None:
        first = {
            "mda_user_id": "user-1",
            "mda_source_provider": "slack",
            "mda_source_thread_id": "slack:team:chan-1",
        }
        second = {**first, "mda_source_thread_id": "slack:team:chan-2"}
        assert _thread_owner_key(first) != _thread_owner_key(second)

    def test_partial_source_stamp_falls_back_to_the_caller(self) -> None:
        # Managed auth rejects one stamp without the other, so this shape only
        # reaches here from a non-channel caller.
        assert (
            _thread_owner_key({"mda_user_id": "user-1", "mda_source_thread_id": "slack:t:c"})
            == "user-1"
        )
        assert (
            _thread_owner_key({"mda_user_id": "user-1", "mda_source_provider": "slack"}) == "user-1"
        )


class TestAssertStoreAccess:
    USER: ClassVar[dict[str, Any]] = {"mda_user_id": "user-1"}
    SERVICE_USER: ClassVar[dict[str, Any]] = {
        "identity": "mda:identity-links",
        "permissions": [],
        "mda_user_id": "mda:identity-links",
    }

    def test_reserves_managed_tables_for_the_service_principal(self) -> None:
        # Deployment-wide managed tables — account links and legacy OAuth tokens
        # for every caller — so an ordinary authenticated caller must never reach
        # them, whatever the project declares about memory. The credentials
        # namespace stays reserved until leftover Connect tokens are scrubbed.
        for namespace in (
            ["mda_identity_links", "slack:T:U"],
            ["mda_github_credentials", "actor:web-user-1"],
        ):
            _assert_store_access(self.SERVICE_USER, {"namespace": namespace})
            with pytest.raises(ManagedAuthError, match="identity boundary"):
                _assert_store_access(self.USER, {"namespace": namespace})

    def test_leaves_ordinary_namespaces_alone(self) -> None:
        # No declaration mounts per-caller state in this release, so there is no
        # per-caller partition to enforce outside the reserved tables.
        for value in (
            {"namespace": ["dep", "user-1"]},
            {"namespace": ["dep", "user-2"]},
            {"namespace": ["anything"]},
            {},
        ):
            _assert_store_access(self.USER, value)


class _SimpleRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_dual_ingress_trusted_wins_under_validated_token() -> None:
    cfg = {
        "ingress": {
            "http": {
                "mode": "validated_token",
                "providers": [
                    {
                        "id": "idp",
                        "jwks": "https://idp.example/.well-known/jwks.json",
                        "claims": {"user": "sub"},
                    }
                ],
            }
        },
        "scope": {},
    }
    identity = asyncio.run(
        resolve_request_identity(cfg, _SimpleRequest(_headers(**{"x-mda-user-id": "web-user-1"})))
    )
    assert identity["user"]["id"] == "web-user-1"


def test_dual_ingress_validated_token_rejects_without_secret_or_bearer() -> None:
    cfg = {
        "ingress": {
            "http": {
                "mode": "validated_token",
                "providers": [{"jwks": "https://x/jwks", "claims": {"user": "sub"}}],
            }
        },
        "scope": {},
    }
    with pytest.raises(ManagedAuthError) as exc:
        asyncio.run(resolve_request_identity(cfg, _SimpleRequest({})))
    assert exc.value.status == 401


def _api_key_cfg() -> dict[str, Any]:
    return {"ingress": {"http": {"mode": "langsmith_api_key"}}, "scope": {}}


def _configure_server_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_identity_auth.INGRESS_SECRET_ENV)
    monkeypatch.setenv(_identity_auth.LANGSMITH_API_KEY_ENV, "server-key")
    monkeypatch.setenv(_identity_auth.LANGSMITH_AUTH_ENDPOINT_ENV, "https://auth.example/base")
    monkeypatch.setenv(_identity_auth.LANGSMITH_TENANT_ID_ENV, "tenant-123")
    monkeypatch.setattr(
        _identity_auth,
        "urlopen",
        lambda *_args, **_kwargs: _LangSmithResponse(b'{"tenant_id":"tenant-123"}'),
    )


def test_server_api_key_trusts_channel_identity_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_server_api_key(monkeypatch)

    def fail_urlopen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("server-key handoff must not call LangSmith auth")

    monkeypatch.setattr(_identity_auth, "urlopen", fail_urlopen)
    identity = asyncio.run(
        resolve_request_identity(
            _api_key_cfg(),
            _SimpleRequest(
                {
                    "x-api-key": "server-key",
                    "x-mda-user-id": "slack-user-1",
                    "x-mda-source-provider": "slack",
                    "x-mda-source-thread-id": "slack:T1:C1:1",
                }
            ),
        )
    )

    assert identity == {
        "user": {"kind": "person", "id": "slack-user-1"},
        "source": {"provider": "slack", "thread_id": "slack:T1:C1:1"},
    }


@pytest.mark.parametrize("provider", ["slack", "photon"])
def test_generated_handoff_secret_trusts_channel_identity_without_remote_auth(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    monkeypatch.setattr(source_providers, "_declared_providers", set())
    if provider == "photon":
        channel = channels.http(
            provider=provider, verify=lambda _ctx: True, parse=lambda _ctx: {"type": "ignore"}
        )
        collect_channels([("channels/inbox.py", SimpleNamespace(channel=channel))])
    _configure_server_api_key(monkeypatch)
    monkeypatch.delenv(_identity_auth.LANGSMITH_API_KEY_ENV)
    monkeypatch.setenv(_identity_auth.HANDOFF_SECRET_ENV, "generated-handoff-secret")

    def fail_urlopen(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("managed handoff must not call LangSmith auth")

    monkeypatch.setattr(_identity_auth, "urlopen", fail_urlopen)
    identity = asyncio.run(
        resolve_request_identity(
            _api_key_cfg(),
            _SimpleRequest(
                {
                    "x-mda-handoff-secret": "generated-handoff-secret",
                    "x-mda-user-id": "slack-user-1",
                    "x-mda-source-provider": provider,
                    "x-mda-source-thread-id": "slack:T1:C1:1",
                }
            ),
        )
    )

    assert identity["user"]["id"] == "slack-user-1"

    assert identity["source"] == {"provider": provider, "thread_id": "slack:T1:C1:1"}


def test_other_api_key_cannot_stamp_reserved_identity_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_server_api_key(monkeypatch)

    with pytest.raises(ManagedAuthError, match="trusted server credential") as exc:
        asyncio.run(
            resolve_request_identity(
                _api_key_cfg(),
                _SimpleRequest({"x-api-key": "another-valid-key", "x-mda-user-id": "victim"}),
            )
        )
    assert exc.value.status == 401


def test_server_api_key_without_reserved_headers_remains_service_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_server_api_key(monkeypatch)
    identity = asyncio.run(
        resolve_request_identity(_api_key_cfg(), _SimpleRequest({"x-api-key": "server-key"}))
    )

    assert identity["user"] == {
        "kind": "service",
        "id": "langsmith:tenant:tenant-123",
    }
    assert identity["source"]["provider"] == "http"


def test_normalize_headers_lowercases_and_decodes() -> None:
    assert _normalize_headers({b"X-MDA-User-Id": b"user-1", "X-Other": "v"}) == {
        "x-mda-user-id": "user-1",
        "x-other": "v",
    }
    assert _normalize_headers(None) == {}


# --- Wiring tests via a fake langgraph_sdk.Auth ---------------------------------


class _RegisteredOn:
    def __init__(self, auth: Auth) -> None:
        # The handlers ``build_managed_auth`` registered on a real ``Auth``.
        self.global_handler = auth._global_handlers[0]
        self.threads_handler = auth._handlers[("threads", "*")][0]
        self.store_handler = auth._handlers[("store", "*")][0]
        self.crons_handler = auth._handlers[("crons", "*")][0]


class _RegisteredAuth:
    def __init__(self, auth: Auth) -> None:
        self.auth = auth
        self.authenticate_handler = auth._authenticate_handler
        self.on = _RegisteredOn(auth)


def _build_auth(identity: Any) -> _RegisteredAuth:
    auth = build_managed_auth(identity)
    assert isinstance(auth, Auth)
    return _RegisteredAuth(auth)


def _treat_as_studio(monkeypatch: pytest.MonkeyPatch, studio: bool) -> None:
    monkeypatch.setattr(_identity_auth, "is_studio_user", lambda _user: studio)


class _Ctx:
    def __init__(self, user: Any, resource: str = "", action: str = "") -> None:
        self.user = user
        self.resource = resource
        self.action = action


@pytest.fixture
def fake_auth() -> _RegisteredAuth:
    return _build_auth(define_identity(auth="backend"))


def test_build_managed_auth_registers_handlers(fake_auth: _RegisteredAuth) -> None:
    assert fake_auth.authenticate_handler is not None
    assert fake_auth.on.threads_handler is not None
    assert fake_auth.on.store_handler is not None
    # Without the catch-all, LangGraph permits every unhandled resource.
    assert fake_auth.on.global_handler is not None


class TestDenyUnlessAssistantRead:
    """`assistants` and `crons` are deploy-owned; no runtime caller may mutate them."""

    @pytest.mark.parametrize("action", ["create", "read", "update", "delete", "search"])
    def test_denies_every_cron_action(self, action: str) -> None:
        # crons:create takes a thread_id, so an open crons resource lets a caller
        # schedule a run against a thread thread-ownership would otherwise deny.
        with pytest.raises(ManagedAuthError, match="not permitted") as exc:
            _deny_unless_assistant_read("crons", action)
        assert exc.value.status == 403

    @pytest.mark.parametrize("action", ["create", "update", "delete"])
    def test_denies_assistant_writes(self, action: str) -> None:
        with pytest.raises(ManagedAuthError, match="not permitted"):
            _deny_unless_assistant_read("assistants", action)

    @pytest.mark.parametrize("action", ["read", "search"])
    def test_allows_assistant_reads(self, action: str) -> None:
        _deny_unless_assistant_read("assistants", action)

    def test_denies_resources_added_by_future_sdk_versions(self) -> None:
        # The point of the catch-all: a resource this release has never heard of
        # must be denied rather than silently permitted.
        with pytest.raises(ManagedAuthError, match="not permitted"):
            _deny_unless_assistant_read("some_future_resource", "read")


def test_global_handler_denies_crons_for_ordinary_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _treat_as_studio(monkeypatch, False)
    auth = _build_auth(define_identity(auth="backend"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.on.global_handler(
                _Ctx({"mda_user_id": "user-123"}, resource="crons", action="create"),
                {"schedule": "* * * * *", "thread_id": "victim-thread"},
            )
        )
    assert exc.value.status_code == 403
    # Assistant reads stay reachable so clients can list the deployment's agents.
    assert (
        asyncio.run(
            auth.on.global_handler(
                _Ctx({"mda_user_id": "user-123"}, resource="assistants", action="search"),
                {},
            )
        )
        is None
    )


def test_global_handler_exempts_studio_and_local_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    # `mda deploy` provisions crons as a workspace/Studio principal, so denying
    # crons outright must not break deploy-time schedule reconciliation.
    _treat_as_studio(monkeypatch, True)
    studio_auth = _build_auth(define_identity(auth="backend"))
    assert (
        asyncio.run(
            studio_auth.on.global_handler(
                _Ctx({"mda_user_id": "studio"}, resource="crons", action="create"), {}
            )
        )
        is None
    )

    _treat_as_studio(monkeypatch, False)
    scoped_auth = _build_auth(define_identity(auth="backend"))
    local_dev_user = {
        "identity": "mda:local-dev",
        "permissions": [],
        "mda_user_id": "mda:local-dev",
        "mda_user_kind": "service",
    }
    ctx = _Ctx(local_dev_user, resource="crons", action="create")
    # Deploy never sets MDA_LOCAL_DEV — without it the principal stays denied.
    with pytest.raises(HTTPException):
        asyncio.run(scoped_auth.on.global_handler(ctx, {}))

    monkeypatch.setenv("MDA_LOCAL_DEV", "1")
    assert asyncio.run(scoped_auth.on.global_handler(ctx, {})) is None


def test_store_filter_denies_reserved_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    # The wired handler refuses the managed tables for ordinary callers.
    _treat_as_studio(monkeypatch, False)
    auth = _build_auth(define_identity(auth="backend"))

    asyncio.run(
        auth.on.store_handler(_Ctx({"mda_user_id": "user-123"}), {"namespace": ["dep", "user-123"]})
    )
    with pytest.raises(HTTPException):
        asyncio.run(
            auth.on.store_handler(
                _Ctx({"mda_user_id": "user-123"}),
                {"namespace": ["mda_identity_links", "slack:T:U"]},
            )
        )


def test_store_filter_reserves_tables_from_studio_and_local_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Studio identity is reachable by anyone holding a workspace LangSmith API
    # key (`x-auth-scheme: langsmith`), so exempting it would expose every
    # caller's account links and legacy OAuth tokens via the Store API.
    _treat_as_studio(monkeypatch, True)
    studio_auth = _build_auth(define_identity(auth="backend"))

    for namespace in (
        ["mda_identity_links", "slack:T:U"],
        ["mda_github_credentials", "actor:web-user-1"],
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                studio_auth.on.store_handler(
                    _Ctx({"mda_user_id": "studio"}), {"namespace": namespace}
                )
            )
        assert exc.value.status_code == 403

    # Ordinary namespaces stay reachable, so Studio keeps working elsewhere.
    assert (
        asyncio.run(
            studio_auth.on.store_handler(_Ctx({"mda_user_id": "studio"}), {"namespace": ["dep"]})
        )
        is None
    )

    _treat_as_studio(monkeypatch, False)
    monkeypatch.setenv("MDA_LOCAL_DEV", "1")
    local_dev_auth = _build_auth(define_identity(auth="backend"))
    local_dev_user = {
        "identity": "mda:local-dev",
        "permissions": [],
        "mda_user_id": "mda:local-dev",
        "mda_user_kind": "service",
    }
    with pytest.raises(HTTPException):
        asyncio.run(
            local_dev_auth.on.store_handler(
                _Ctx(local_dev_user), {"namespace": ["mda_identity_links", "slack:T:U"]}
            )
        )


def test_store_filter_still_admits_identity_links_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _treat_as_studio(monkeypatch, False)
    auth = _build_auth(define_identity(auth="backend"))
    assert (
        asyncio.run(
            auth.on.store_handler(
                _Ctx({"mda_user_id": _identity_auth.IDENTITY_LINKS_SERVICE_USER}),
                {"namespace": ["mda_identity_links", "slack:T:U"]},
            )
        )
        is None
    )


def test_authenticate_happy_path(fake_auth: _RegisteredAuth) -> None:
    user = asyncio.run(fake_auth.authenticate_handler(headers=_headers()))
    assert user["mda_user_id"] == "user-123"


def test_authenticate_translates_to_http_exception(fake_auth: _RegisteredAuth) -> None:
    headers = _headers()
    del headers["x-mda-ingress-secret"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fake_auth.authenticate_handler(headers=headers))
    assert exc.value.status_code == 401


def test_authenticate_keeps_verification_unavailable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_identity_auth.LANGSMITH_AUTH_ENDPOINT_ENV, "https://auth.example/api/v1")
    monkeypatch.setenv(_identity_auth.LANGSMITH_TENANT_ID_ENV, "tenant-123")
    monkeypatch.setattr(
        _identity_auth,
        "urlopen",
        Mock(side_effect=HTTPError("https://auth.example", 503, "unavailable", None, None)),
    )
    monkeypatch.setattr(_identity_auth.time, "sleep", Mock())
    registered = _build_auth(define_identity(auth={"mode": "langsmith_api_key"}))

    with pytest.raises(StarletteHTTPException) as exc:
        asyncio.run(registered.authenticate_handler(headers={"x-api-key": "test-key"}))
    assert exc.value.status_code == 503


def test_authenticate_keeps_configuration_errors_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_identity_auth.LANGSMITH_AUTH_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_identity_auth.LOCAL_DEV_ENV, raising=False)
    registered = _build_auth(define_identity(auth={"mode": "langsmith_api_key"}))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(registered.authenticate_handler(headers={"x-api-key": "test-key"}))
    assert exc.value.status_code == 500


def test_thread_filter_sets_owner(fake_auth: _RegisteredAuth) -> None:
    value: dict[str, Any] = {}
    result = asyncio.run(fake_auth.on.threads_handler(_Ctx({"mda_user_id": "user-123"}), value))
    assert result == {"owner": "user-123"}
    assert value["metadata"]["owner"] == "user-123"


def test_thread_filter_overwrites_client_supplied_owner(fake_auth: _RegisteredAuth) -> None:
    """Clients must not be able to forge ownership or widen search via metadata.owner."""
    create_value: dict[str, Any] = {"metadata": {"owner": "victim", "title": "forged"}}
    result = asyncio.run(
        fake_auth.on.threads_handler(_Ctx({"mda_user_id": "attacker"}), create_value)
    )
    assert result == {"owner": "attacker"}
    assert create_value["metadata"] == {"owner": "attacker", "title": "forged"}

    search_value: dict[str, Any] = {"metadata": {"owner": "victim"}, "limit": 50}
    search_result = asyncio.run(
        fake_auth.on.threads_handler(_Ctx({"mda_user_id": "attacker"}), search_value)
    )
    assert search_result == {"owner": "attacker"}
    # Defense in depth vs Agent Server AND of client metadata + auth filter.
    assert search_value["metadata"]["owner"] == "attacker"


def test_thread_filters_are_distinct_per_actor(fake_auth: _RegisteredAuth) -> None:
    filter_a = asyncio.run(
        fake_auth.on.threads_handler(_Ctx({"mda_user_id": "actor-a"}), {"metadata": {}})
    )
    filter_b = asyncio.run(
        fake_auth.on.threads_handler(_Ctx({"mda_user_id": "actor-b"}), {"metadata": {}})
    )
    assert filter_a == {"owner": "actor-a"}
    assert filter_b == {"owner": "actor-b"}
    assert filter_a != filter_b


def test_thread_filter_keeps_channel_events_bypass_principal_scoped(
    fake_auth: _RegisteredAuth,
) -> None:
    """Channel-events auth bypass must not imply unscoped thread access."""
    channel = asyncio.run(
        fake_auth.on.threads_handler(_Ctx(channel_events_bypass_user()), {"metadata": {}})
    )
    assert channel == {"owner": CHANNEL_EVENTS_USER}


def test_thread_filter_unscopes_studio_reads_but_scopes_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _treat_as_studio(monkeypatch, True)
    monkeypatch.delenv(_identity_auth.LOCAL_DEV_ENV, raising=False)
    studio_auth = _build_auth(define_identity(auth="backend"))
    studio_user = {
        "kind": "StudioUser",
        "display_name": "langgraph-studio-user",
        "identity": "langgraph-studio-user",
        "ls_user_id": "canonical-user",
        "permissions": [],
        "is_authenticated": True,
    }
    assert (
        asyncio.run(
            studio_auth.on.threads_handler(
                _Ctx(studio_user, resource="threads", action="read"), None
            )
        )
        is None
    )
    assert (
        asyncio.run(
            studio_auth.on.threads_handler(
                _Ctx(studio_user, resource="threads", action="search"),
                {"metadata": {"owner": "victim"}},
            )
        )
        is None
    )

    value: dict[str, Any] = {"metadata": {"owner": "victim"}}
    assert asyncio.run(
        studio_auth.on.threads_handler(
            _Ctx(studio_user, resource="threads", action="create"), value
        )
    ) == {"owner": "canonical-user"}
    assert value["metadata"]["owner"] == "canonical-user"


def test_thread_filter_unscopes_local_dev_only_when_intended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _treat_as_studio(monkeypatch, False)
    scoped_auth = _build_auth(define_identity(auth="backend"))
    local_dev_user = {
        "identity": "mda:local-dev",
        "permissions": [],
        "mda_user_id": "mda:local-dev",
        "mda_user_kind": "service",
    }
    # Without MDA_LOCAL_DEV the service principal stays scoped (deploy-safe).
    monkeypatch.delenv(_identity_auth.LOCAL_DEV_ENV, raising=False)
    assert asyncio.run(scoped_auth.on.threads_handler(_Ctx(local_dev_user), {"metadata": {}})) == {
        "owner": "mda:local-dev"
    }

    monkeypatch.setenv(_identity_auth.LOCAL_DEV_ENV, "1")
    assert (
        asyncio.run(scoped_auth.on.threads_handler(_Ctx(local_dev_user), {"metadata": {}})) is None
    )


class TestChannelEventsRequest:
    @pytest.mark.parametrize(
        "prefix", ["/lgp/test-agent", "/lgp/test-agent/", "/nested/deployments/test-agent"]
    )
    def test_matches_configured_mount(self, monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
        monkeypatch.setenv("MOUNT_PREFIX", prefix)
        for path in [
            "/channels/slack/events",
            "/v1/channels/team-chat/events",
            "/connectors/github/events",
        ]:
            for mounted_path in [
                path,
                f"{prefix.rstrip('/')}{path}",
                f"{prefix.rstrip('/')}{path}/",
            ]:
                assert is_channel_events_request("POST", mounted_path) is True

    def test_rejects_other_mounts_and_protected_routes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MOUNT_PREFIX", "/lgp/test-agent")
        for path in [
            "/other/v1/channels/slack/events",
            "/lgp/other-agent/v1/channels/slack/events",
            "/lgp/test-agent-other/v1/channels/slack/events",
            "/lgp/test-agent/threads",
            "/lgp/test-agent/v1/channels/slack/events/extra",
        ]:
            assert is_channel_events_request("POST", path) is False
        assert is_channel_events_request("GET", "/lgp/test-agent/v1/channels/slack/events") is False

    @pytest.mark.parametrize("prefix", ["", "/", "lgp/test-agent"])
    def test_does_not_strip_empty_or_relative_mount(
        self, monkeypatch: pytest.MonkeyPatch, prefix: str
    ) -> None:
        monkeypatch.setenv("MOUNT_PREFIX", prefix)
        assert (
            is_channel_events_request("POST", "/lgp/test-agent/v1/channels/slack/events") is False
        )
        assert is_channel_events_request("POST", "/v1/channels/slack/events") is True

    def test_matches_channel_events_path(self) -> None:
        assert is_channel_events_request("POST", "/channels/slack/events") is True
        assert is_channel_events_request("POST", "/channels/slack/events/") is True
        assert is_channel_events_request("POST", "/v1/channels/slack/events") is True
        assert is_channel_events_request("POST", "/connectors/github/events") is True

    def test_rejects_non_events_paths(self) -> None:
        assert is_channel_events_request("GET", "/channels/slack/events") is False
        assert is_channel_events_request("POST", "/connectors/github") is False
        assert is_channel_events_request("POST", "/channels/slack/events/extra") is False
        assert is_channel_events_request("POST", "/threads") is False
        assert is_channel_events_request(None, None) is False


def test_authenticate_bypasses_auth_for_channel_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _treat_as_studio(monkeypatch, False)
    auth = _build_auth(define_identity(auth="backend"))
    user = asyncio.run(
        auth.authenticate_handler(headers={}, path="/channels/slack/events", method="POST")
    )
    assert user["mda_user_id"] == CHANNEL_EVENTS_USER


def test_trigger_headers_are_route_scoped_and_langchain_key_authenticates_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _treat_as_studio(monkeypatch, False)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "hosted-server-key")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    auth = _build_auth(define_identity(auth="backend"))

    bypass = asyncio.run(
        auth.authenticate_handler(
            headers={"x-service-key": "present", "x-user-id": "user-1"},
            path="/v1/channels/slack/events",
            method="POST",
        )
    )
    assert bypass["mda_user_id"] == CHANNEL_EVENTS_USER

    for path in ("/threads", "/runs/run-1"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                auth.authenticate_handler(
                    headers={"x-service-key": "present", "x-user-id": "user-1"},
                    path=path,
                    method="GET",
                )
            )
        assert exc.value.status_code == 401

    loopback = asyncio.run(
        auth.authenticate_handler(
            headers={
                "x-api-key": "hosted-server-key",
                "x-mda-user-id": "user-1",
                "x-mda-source-provider": "slack",
                "x-mda-source-thread-id": "slack:v1:A1:T1:C1:171.1",
            },
            path="/threads",
            method="POST",
        )
    )
    assert loopback["mda_user_id"] == "user-1"


def test_validated_token_missing_bearer_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    _treat_as_studio(monkeypatch, False)
    identity = define_identity(
        auth=cast(
            "Any",
            [{"jwks": "https://x/jwks", "audience": "api://default", "claims": {"user": "sub"}}],
        ),
    )
    auth = _build_auth(identity)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.authenticate_handler(headers={}))
    assert exc.value.status_code == 401
