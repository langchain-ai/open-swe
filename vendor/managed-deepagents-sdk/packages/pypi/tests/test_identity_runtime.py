"""Unit tests for the pure runtime-seam identity helpers."""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, ValuesView
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda

import managed_deepagents._identity_runtime as identity_runtime
from managed_deepagents import CredentialStoreError
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._connections import (
    _resolve_agent_auth_config,
    resolve_credential_principal_id,
    resolve_owner_id,
)
from managed_deepagents._identity_runtime import (
    build_managed_server_info,
    build_principal_envelope,
    build_runtime_identity,
    principal_envelope_from_server_info,
    resolve_auth_user,
    resolve_runtime_configurable,
    sanitize_configurable,
)
from managed_deepagents._managed_thread import on_channel_thread
from managed_deepagents._thread_record import next_thread_record, thread_record_metadata


class TestBuildRuntimeIdentity:
    def test_builds_full_envelope(self) -> None:
        ident = build_runtime_identity(
            {
                "langgraph_auth_user": {
                    "mda_user_id": "u1",
                    "mda_user_email": "a@b.com",
                    "mda_claims": {"role": "admin"},
                    "mda_groups": ["billing"],
                    "mda_identity_provider": "supabase",
                    "mda_identity_issuer": "https://ref.supabase.co/auth/v1",
                    "mda_identity_subject": "sub-1",
                },
                "thread_id": "t-42",
            }
        )
        caller = {"kind": "person", "id": "u1", "email": "a@b.com"}
        assert ident == {
            "user": caller,
            "source": {"provider": "http", "thread_id": "t-42"},
            "groups": ("billing",),
            "claims": {"role": "admin"},
            "identity_provider": "supabase",
            "identity_issuer": "https://ref.supabase.co/auth/v1",
            "identity_subject": "sub-1",
        }

    def test_aliases_share_one_frozen_mapping(self) -> None:
        ident = build_runtime_identity({"langgraph_auth_user": {"mda_user_id": "u1"}})
        assert ident is not None
        assert ident["user"] is ident["user"]

    def test_marks_service_principal(self) -> None:
        ident = build_runtime_identity(
            {"langgraph_auth_user": {"mda_user_id": "svc", "mda_user_kind": "service"}}
        )
        assert ident is not None
        assert ident["user"]["kind"] == "service"
        assert "organization" not in ident
        assert "groups" not in ident

    def test_source_provider_allowlist(self) -> None:
        base = {"langgraph_auth_user": {"mda_user_id": "u1"}}
        assert (
            build_runtime_identity(
                {
                    "langgraph_auth_user": {
                        "mda_user_id": "u1",
                        "mda_source_provider": "slack",
                    }
                }
            )["source"]["provider"]
            == "slack"
        )
        assert (
            build_runtime_identity(
                {
                    "langgraph_auth_user": {
                        "mda_user_id": "u1",
                        "mda_source_provider": "github",
                    }
                }
            )["source"]["provider"]
            == "github"
        )
        assert (
            build_runtime_identity(
                {
                    "langgraph_auth_user": {
                        "mda_user_id": "u1",
                        "mda_source_provider": "evil",
                    }
                }
            )["source"]["provider"]
            == "http"
        )
        # Legacy top-level stamp still accepted until callers finish migrating.
        assert (
            build_runtime_identity({**base, "mda_source_provider": "schedule"})["source"][
                "provider"
            ]
            == "schedule"
        )

    def test_prefers_trusted_source_thread(self) -> None:
        ident = build_runtime_identity(
            {
                "langgraph_auth_user": {
                    "mda_user_id": "u1",
                    "mda_source_thread_id": "slack:v1:abc",
                },
                "thread_id": "langgraph-thread",
            }
        )
        assert ident is not None
        assert ident["source"]["thread_id"] == "slack:v1:abc"

    def test_returns_none_without_actor(self) -> None:
        assert build_runtime_identity(None) is None
        assert build_runtime_identity({}) is None
        assert build_runtime_identity({"langgraph_auth_user": {"mda_user_id": ""}}) is None

    def test_maps_studio_user_to_studio_service_principal(self) -> None:
        ident = build_runtime_identity(
            {
                "langgraph_auth_user": {
                    "kind": "StudioUser",
                    "identity": "langgraph-studio-user",
                    "permissions": [],
                }
            }
        )
        assert ident is not None
        assert ident["user"] == {
            "kind": "service",
            "id": "langgraph-studio-user",
        }
        assert ident["source"]["provider"] == "studio"

    def test_envelope_is_read_only(self) -> None:
        ident = build_runtime_identity(
            {
                "langgraph_auth_user": {
                    "mda_user_id": "u1",
                    "mda_claims": {"role": "admin"},
                }
            }
        )
        assert ident is not None
        with pytest.raises(TypeError):
            ident["user"]["id"] = "spoof"  # type: ignore[index]
        with pytest.raises(TypeError):
            ident["claims"]["role"] = "root"  # type: ignore[index]


class TestResolveRuntimeConfigurable:
    def test_prefers_runtime_over_ambient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            identity_runtime,
            "_ambient_configurable",
            lambda: {"langgraph_auth_user": {"mda_user_id": "ambient"}},
        )
        runtime = SimpleNamespace(configurable={"langgraph_auth_user": {"mda_user_id": "runtime"}})
        assert resolve_runtime_configurable(runtime) == {
            "langgraph_auth_user": {"mda_user_id": "runtime"}
        }

    def test_falls_back_to_ambient_when_only_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Memory/skills middleware may only pass {state}; ambient config must win.
        monkeypatch.setattr(
            identity_runtime,
            "_ambient_configurable",
            lambda: {"langgraph_auth_user": {"mda_user_id": "alice"}},
        )
        assert resolve_runtime_configurable(SimpleNamespace(state={})) == {
            "langgraph_auth_user": {"mda_user_id": "alice"}
        }

    def test_falls_back_to_ambient_when_configurable_lacks_auth_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Python MemoryMiddleware builds ToolRuntime(config=runnable_config). When
        # that bag is empty/partial, returning it would skip ambient and break
        # first-turn actor memory remounts.
        monkeypatch.setattr(
            identity_runtime,
            "_ambient_configurable",
            lambda: {"langgraph_auth_user": {"mda_user_id": "alice"}},
        )
        runtime = SimpleNamespace(config={"configurable": {}})
        assert resolve_runtime_configurable(runtime) == {
            "langgraph_auth_user": {"mda_user_id": "alice"}
        }


class TestMemoryHubRepoHandle:
    def test_strips_client_identity_keys(self) -> None:
        out = sanitize_configurable(
            {
                "thread_id": "t-1",
                "user_id": "spoof",
                "organization_id": "spoof",
                "mda_user_id": "spoof",
                "mda_claims": {"role": "admin"},
                "mda_schedule": {"name": "forged", "deliver_to": {"channel": "slack"}},
                "langgraph_auth_user": {"mda_user_id": "real"},
            }
        )
        assert out == {"thread_id": "t-1", "langgraph_auth_user": {"mda_user_id": "real"}}

    def test_strips_current_key_spellings(self) -> None:
        out = sanitize_configurable(
            {
                "thread_id": "t-1",
                "user_id": "spoof",
                "organization_id": "spoof",
                "mda_user_id": "spoof",
                "mda_organization_id": "spoof",
                "mda_groups": ["admins"],
                "mda_user_kind": "service",
                "mda_user_email": "spoof@example.com",
            }
        )
        assert out == {"thread_id": "t-1"}

    def test_handles_none(self) -> None:
        assert sanitize_configurable(None) == {}


class _ProxyUser:
    """Stand-in for LangGraph's ``ProxyUser``: attribute + ``get`` access, no ``Mapping``.

    LangGraph normalizes a custom-auth handler's dict through ``normalize_user``
    before it lands in ``configurable["langgraph_auth_user"]``, so the trusted
    caller the runtime sees is a ``BaseUser`` object rather than a mapping.
    """

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> object:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    def dict(self) -> dict[str, object]:
        return dict(self._data)


class _StudioUser:
    """Stand-in for ``langgraph_api.auth.studio_user.StudioUser``.

    That class subclasses ``langgraph_sdk.auth.types.StudioUser`` and layers the
    mapping protocol over ``dict()``, so it reads like a mapping without being a
    registered ``Mapping``. Reproduced here because ``langgraph_api`` is not a
    test dependency, and the gap between "duck-types as a mapping" and
    "``isinstance(x, Mapping)``" is exactly what regressed.
    """

    __slots__ = ("_is_authenticated", "ls_user_id", "username")

    def __init__(
        self, username: str, *, is_authenticated: bool = False, ls_user_id: str | None = None
    ) -> None:
        self.ls_user_id = ls_user_id
        self.username = username
        self._is_authenticated = is_authenticated

    @property
    def identity(self) -> str:
        return self.username

    @property
    def display_name(self) -> str:
        return self.username

    @property
    def is_authenticated(self) -> bool:
        return self._is_authenticated

    @property
    def permissions(self) -> tuple[str, ...]:
        return ("authenticated",) if self._is_authenticated else ()

    def dict(self) -> dict[str, object]:
        return {
            "kind": "StudioUser",
            "ls_user_id": self.ls_user_id,
            "is_authenticated": self.is_authenticated,
            "display_name": self.display_name,
            "identity": self.identity,
            "permissions": self.permissions,
        }

    def get(self, key: str, default: object = None) -> object:
        return self.dict().get(key, default)

    def __getitem__(self, key: str) -> object:
        return self.dict()[key]

    def __contains__(self, key: str) -> bool:
        return key in self.dict()

    def __iter__(self) -> Iterator[str]:
        return iter(self.dict())

    def __len__(self) -> int:
        return len(self.dict())

    def keys(self) -> KeysView[str]:
        return self.dict().keys()

    def items(self) -> ItemsView[str, object]:
        return self.dict().items()

    def values(self) -> ValuesView[object]:
        return self.dict().values()


class TestStudioAuthUserObject:
    """Studio principals arrive as an object, not a dict.

    The Agent Server injects ``StudioUser`` into
    ``configurable["langgraph_auth_user"]`` on both construction sites:
    ``StudioUser("langgraph-studio-user")`` for the custom-auth path
    (``x-auth-scheme: langsmith``), and ``StudioUser(auth_dict["user_id"])`` on
    the LangSmith backend, where ``identity`` is a real workspace user id and
    ``kind`` is the only usable signal. Gating detection on
    ``isinstance(user, Mapping)`` rejected both, leaving no resolvable actor, so
    ``connections.get(slug, {"type": "user"})`` failed from every Studio
    session.
    """

    def test_double_matches_the_real_shape(self) -> None:
        # Pins the precondition the regression turned on: mapping-like, but not
        # a Mapping. If this ever becomes True the bug cannot recur, and this
        # whole class is redundant.
        user = _StudioUser("langgraph-studio-user")
        assert not isinstance(user, Mapping)
        assert user.get("kind") == "StudioUser"

    @pytest.mark.parametrize(
        ("username", "kind"),
        [("langgraph-studio-user", "service"), ("ls-user-abc123", "person")],
        ids=["local-dev", "langsmith-backend"],
    )
    def test_resolves_studio_principal(self, username: str, kind: str) -> None:
        ident = build_runtime_identity(
            {
                "langgraph_auth_user": _StudioUser(
                    username,
                    is_authenticated=kind == "person",
                    ls_user_id="canonical-user-abc123" if kind == "person" else None,
                )
            }
        )
        assert ident is not None
        assert ident["user"] == {
            "kind": kind,
            "id": "canonical-user-abc123" if kind == "person" else "langgraph-studio-user",
        }
        if kind == "person":
            assert ident["identity_provider"] == "langsmith"
            assert ident["identity_subject"] == "canonical-user-abc123"
        assert ident["source"]["provider"] == "studio"

    def test_resolves_studio_user_without_login_id(self) -> None:
        configurable = {
            "langgraph_auth_user": {
                "kind": "StudioUser",
                "is_authenticated": True,
                "ls_user_id": "canonical-user",
            }
        }
        identity = build_runtime_identity(configurable)
        assert identity is not None
        assert identity["user"] == {"kind": "person", "id": "canonical-user"}
        user = resolve_auth_user(configurable)
        assert user is not None
        assert user["identity"] == "canonical-user"

    def test_resolves_auth_user(self) -> None:
        user = resolve_auth_user(
            {
                "langgraph_auth_user": _StudioUser(
                    "ls-user-abc123", is_authenticated=True, ls_user_id="canonical-user-abc123"
                )
            }
        )
        assert user is not None
        assert user["identity"] == "canonical-user-abc123"
        assert user["mda_user_id"] == "canonical-user-abc123"

    def test_builds_principal_envelope_as_user_authority(self) -> None:
        envelope = build_principal_envelope(
            {
                "langgraph_auth_user": _StudioUser(
                    "ls-user-abc123", is_authenticated=True, ls_user_id="canonical-user-abc123"
                )
            },
            "agent-1",
        )
        assert envelope is not None
        assert envelope["principal"]["id"] == "canonical-user-abc123"
        assert envelope["principal"]["kind"] == "person"
        assert envelope["subject"] == {
            "authority": "user",
            "user_id": "canonical-user-abc123",
            "agent_id": "agent-1",
        }
        info = build_managed_server_info(
            None,
            {
                "langgraph_auth_user": _StudioUser(
                    "ls-user-abc123", is_authenticated=True, ls_user_id="canonical-user-abc123"
                )
            },
        )
        assert info is not None
        assert info.principal.kind == "person"
        assert info.subject.authority == "user"
        assert info.source.provider == "studio"

    @pytest.mark.parametrize(
        "user",
        [
            {"identity": "ls-user-abc123", "is_authenticated": True},
            {"identity": "ls-user-abc123", "is_authenticated": True, "ls_user_id": None},
            {"identity": "ls-user-abc123", "is_authenticated": True, "ls_user_id": "   "},
            {"identity": "ls-user-abc123", "is_authenticated": True, "ls_user_id": 42},
            {"identity": "ls-user-abc123", "is_authenticated": True, "ls_user_id": ""},
            {
                "identity": "ls-user-abc123",
                "is_authenticated": False,
                "ls_user_id": "canonical-user",
            },
            {"identity": "ls-user-abc123", "ls_user_id": "canonical-user"},
            {
                "identity": "ls-user-abc123",
                "is_authenticated": "true",
                "ls_user_id": "canonical-user",
            },
        ],
    )
    def test_keeps_unverified_studio_callers_as_services(self, user: dict[str, object]) -> None:
        configurable = {"langgraph_auth_user": {"kind": "StudioUser", **user}}
        identity = build_runtime_identity(configurable)
        envelope = build_principal_envelope(configurable)
        assert identity is not None
        assert envelope is not None
        assert identity["user"]["kind"] == "service"
        assert envelope["subject"]["authority"] == "agent"

    def test_prefers_the_sdk_isinstance_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `langgraph_sdk` is absent from the test env, so the structural
        # fallback carries the cases above. In a deployment the SDK is present
        # and `is_studio_user` answers first; cover that path too, with a user
        # whose `kind` and `identity` would both otherwise miss.
        opaque = SimpleNamespace(identity="ls-user-abc123")
        monkeypatch.setattr(identity_runtime, "is_studio_user", lambda user: user is opaque)
        ident = build_runtime_identity({"langgraph_auth_user": opaque})
        assert ident is not None
        assert ident["user"] == {"kind": "service", "id": "langgraph-studio-user"}
        assert ident["source"]["provider"] == "studio"

    def test_ignores_an_unrelated_object(self) -> None:
        assert build_runtime_identity({"langgraph_auth_user": SimpleNamespace()}) is None
        assert (
            build_runtime_identity({"langgraph_auth_user": _ProxyUser({"identity": "u1"})}) is None
        )

    def test_owner_id_resolves_to_the_studio_principal(self) -> None:
        # Explicit connection owners must match the verified Studio user.
        identity = build_runtime_identity(
            {
                "langgraph_auth_user": _StudioUser(
                    "ls-user-abc123", is_authenticated=True, ls_user_id="canonical-user-abc123"
                )
            }
        )

        assert identity is not None
        assert (
            resolve_owner_id({"type": "user"}, identity, "deployment-1") == "canonical-user-abc123"
        )

    def test_credential_grant_still_refuses_a_service_principal(self) -> None:
        # The local Studio placeholder is not a verified person, even if an
        # authentication flag is present.
        identity = build_runtime_identity(
            {"langgraph_auth_user": _StudioUser("langgraph-studio-user", is_authenticated=True)}
        )
        assert identity is not None
        assert identity["user"]["kind"] == "service"

        config = _resolve_agent_auth_config(
            {
                "LANGSMITH_API_KEY": "ls-key",
                "LANGSMITH_WORKSPACE_ID": "workspace-1",
                "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
            }
        )

        with pytest.raises(CredentialStoreError, match="require a person principal"):
            resolve_credential_principal_id(identity, config)

    def test_cannot_claim_another_owner_id(self) -> None:
        identity = build_runtime_identity(
            {
                "langgraph_auth_user": _StudioUser(
                    "ls-user-abc123", is_authenticated=True, ls_user_id="canonical-user-abc123"
                )
            }
        )

        with pytest.raises(CredentialStoreError, match="must match the authenticated principal"):
            resolve_owner_id({"type": "user", "id": "someone-else"}, identity, "deployment-1")


class TestNonMappingAuthUser:
    def test_resolves_auth_user_from_proxy_user(self) -> None:
        user = resolve_auth_user(
            {
                "langgraph_auth_user": _ProxyUser(
                    {"identity": "u1", "mda_user_id": "u1", "mda_user_email": "a@b.com"}
                )
            }
        )
        assert user is not None
        assert user["mda_user_id"] == "u1"
        assert user["mda_user_email"] == "a@b.com"
        assert user["permissions"] == ()

    def test_builds_principal_envelope_from_proxy_user(self) -> None:
        envelope = build_principal_envelope(
            {
                "langgraph_auth_user": _ProxyUser(
                    {
                        "identity": "u1",
                        "mda_user_id": "u1",
                        "mda_claims": {"sub": "u1"},
                    }
                )
            },
            "agent-1",
        )
        assert envelope is not None
        assert envelope["principal"] == {
            "id": "u1",
            "kind": "person",
            "claims": {"sub": "u1"},
        }
        assert envelope["subject"] == {
            "authority": "user",
            "agent_id": "agent-1",
            "user_id": "u1",
        }

    def test_server_info_exposes_principal_for_proxy_user(self) -> None:
        info = build_managed_server_info(
            None,
            {"langgraph_auth_user": _ProxyUser({"identity": "u1", "mda_user_id": "u1"})},
        )
        assert info is not None
        assert info.principal.id == "u1"

    def test_returns_none_without_a_caller(self) -> None:
        assert resolve_auth_user({"langgraph_auth_user": _ProxyUser({"identity": "u1"})}) is None
        assert build_principal_envelope({}) is None


class TestServerInfoView:
    """The attribute view authors read, and how it maps back to the wire shape."""

    @staticmethod
    def _info(**user: object) -> object:
        info = build_managed_server_info(
            None,
            {"langgraph_auth_user": {"identity": "u1", "mda_user_id": "u1", **user}},
        )
        assert info is not None
        return info

    def test_claims_stay_subscriptable(self) -> None:
        """Namespaced IdP claims are not valid Python identifiers."""
        claims = self._info(
            mda_claims={"role": "admin", "https://acme.com/tenant": "t1"}
        ).principal.claims
        assert claims["role"] == "admin"
        assert claims["https://acme.com/tenant"] == "t1"

    def test_claims_are_immutable(self) -> None:
        claims = self._info(mda_claims={"role": "admin"}).principal.claims
        with pytest.raises(TypeError):
            claims["role"] = "root"  # type: ignore[index]

    def test_folds_email_and_groups_into_claims(self) -> None:
        claims = self._info(mda_user_email="a@b.com", mda_groups=["billing"]).principal.claims
        assert claims["email"] == "a@b.com"
        assert claims["groups"] == ("billing",)

    def test_identity_fields_use_attribute_access(self) -> None:
        info = self._info()
        assert info.principal.id == "u1"
        assert info.principal.kind == "person"
        assert info.subject.authority == "user"
        assert info.link.status == "not_required"

    def test_normalizes_an_unmapped_groups_claim(self) -> None:
        """Mapping ``groups`` is optional, so the raw claim can be a delimited string."""
        claims = self._info(mda_claims={"groups": "billing, ops"}).principal.claims
        assert claims["groups"] == ("billing", "ops")

    def test_drops_a_groups_claim_that_cannot_normalize(self) -> None:
        claims = self._info(mda_claims={"groups": {"billing": True}}).principal.claims
        assert "groups" not in claims

    def test_drops_a_non_string_email_claim(self) -> None:
        claims = self._info(mda_claims={"email": 42}).principal.claims
        assert "email" not in claims

    def test_absent_optionals_read_as_none(self) -> None:
        """A service principal has no ``user_id``, and a ``not_required`` link no ends."""
        info = self._info(mda_user_kind="service")
        assert info.subject.user_id is None
        assert info.link.from_ is None
        assert info.link.to is None

    def test_native_ids_are_empty_when_platform_is_absent(self) -> None:
        """Native string IDs use empty values when the platform has no metadata."""
        info = self._info()
        assert info.assistant_id == ""
        assert info.graph_id == ""
        assert info.subject.agent_id is None

    def test_platform_agent_id_survives(self) -> None:
        info = build_managed_server_info(
            SimpleNamespace(server_info=SimpleNamespace(assistant_id="a1", graph_id="g1")),
            {"langgraph_auth_user": {"identity": "u1", "mda_user_id": "u1"}},
        )
        assert info is not None
        assert info.assistant_id == "a1"
        assert info.subject.agent_id == "a1"

    def test_source_replaces_the_transport_stamps(self) -> None:
        info = self._info(mda_source_provider="slack", mda_source_thread_id="C1")
        assert info.source.provider == "slack"
        assert info.source.thread_id == "C1"

    def test_source_defaults_to_http(self) -> None:
        info = self._info()
        assert info.source.provider == "http"
        assert info.source.thread_id is None

    def test_present_optionals_survive(self) -> None:
        assert self._info().subject.user_id == "u1"

    def test_filled_optionals_stay_off_the_wire(self) -> None:
        """Agent Auth omits absent envelope keys rather than sending nulls."""
        envelope = principal_envelope_from_server_info(self._info(mda_user_kind="service"))
        assert envelope is not None
        assert envelope["subject"] == {"authority": "agent"}
        assert envelope["link"] == {"status": "not_required"}

    def test_round_trips_to_the_wire_envelope(self) -> None:
        """``from_`` is a Python rename; the wire envelope must say ``from``."""
        info = SimpleNamespace(
            principal={"id": "u1", "kind": "person", "claims": {}},
            subject={"authority": "user", "agent_id": "", "user_id": "u1"},
            link=SimpleNamespace(status="linked", from_="github", to="u1"),
        )
        envelope = principal_envelope_from_server_info(info)
        assert envelope is not None
        assert envelope["link"] == {"status": "linked", "from": "github", "to": "u1"}


def test_on_channel_thread_reads_the_thread_record_from_run_metadata() -> None:
    register_channel_messaging([SimpleNamespace(name="support", kind="slack")])
    binding: Any = {
        "name": "support",
        "provider": "slack",
        "source_thread_key": "source",
        "delivery_id": "delivery",
        "transport": "direct",
        "target": {},
    }
    record = thread_record_metadata(next_thread_record(None, binding, "alice"))
    check = RunnableLambda(lambda _input: on_channel_thread())

    # A Studio resume has no live delivery; the record names the conversation.
    assert check.invoke({}, {"metadata": record}) is True
    assert check.invoke({}, {"metadata": {"mda_channel_thread": "true"}}) is False
    # Outside a run there is no conversation.
    assert on_channel_thread() is False
