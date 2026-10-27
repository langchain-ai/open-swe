"""Pure runtime-seam helpers for managed identity.

These build ``runtime.server_info.principal`` / ``subject`` / ``link`` (author-facing
Agent Auth fields, flattened) and ``runtime.server_info.user`` (LangGraph AuthUser
transport) from the trusted auth principal, and scrub client-supplied identity
keys. ``build_runtime_identity`` remains for HTTP connector handlers that still
take a frozen envelope as a function argument.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, Protocol, cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config
from langgraph_sdk.auth import is_studio_user
from langgraph_sdk.auth.types import BaseUser

from managed_deepagents.identity import ManagedServerInfo
from managed_deepagents.identity.claims import normalize_groups


class RuntimeConfigLike(Protocol):
    """LangGraph tool/middleware runtime or flat configurable carrier.

    Mirrors TypeScript ``RuntimeConfigLike``: either a flat ``configurable`` bag
    or a nested ``config.configurable`` (``ToolRuntime``).
    """

    @property
    def configurable(self) -> Mapping[str, object] | None: ...

    @property
    def config(self) -> RunnableConfig | Mapping[str, object] | None: ...

    @property
    def server_info(self) -> object | None: ...


if TYPE_CHECKING:
    from managed_deepagents.identity import (
        JsonObject,
        LinkView,
        PrincipalEnvelope,
        PrincipalView,
        RuntimeIdentity,
        RuntimeSource,
        RuntimeUser,
        SourceProvider,
        SourceView,
        SubjectView,
    )

__all__ = [
    "build_identity_source",
    "build_managed_server_info",
    "build_principal_envelope",
    "build_runtime_identity",
    "principal_envelope_from_server_info",
    "resolve_auth_user",
    "resolve_runtime_configurable",
    "sanitize_configurable",
]

#: Identity keys a client must never assert via ``config.configurable``; only the
#: custom-auth handler (``langgraph_auth_user``) is trusted.
RESERVED_CONFIGURABLE_KEYS = frozenset(
    {
        "user_id",
        "organization_id",
        "groups",
        "mda_user_id",
        "mda_organization_id",
        "mda_groups",
        "mda_user_kind",
        "mda_user_email",
        "mda_claims",
        "mda_source_provider",
        "mda_source_thread_id",
        # Legacy binding fields cannot grant channel authority.
        "mda_channel_binding",
        # Stamped by compiled cron entries; never client-asserted.
        "mda_schedule",
    }
)

#: Source channels a run can originate from (``runtime.server_info`` / identity source).


def resolve_auth_user(
    configurable: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    """Resolve the trusted auth user for ``runtime.server_info.user``.

    Returns ``None`` when there is no resolvable caller (identity is opt-in).
    The returned mapping is read-only. Prefer ``build_principal_envelope`` /
    ``server_info.principal`` for author-facing identity.
    """
    user = (configurable or {}).get("langgraph_auth_user")
    studio = is_studio_auth_user(user)
    user_id = _runtime_user_id(user)
    fields = _auth_user_fields(user)
    if fields is None or not user_id:
        return None
    identity = _field(user, "identity")
    if studio or not identity or not str(identity).strip():
        identity = str(user_id)
    permissions = fields.get("permissions")
    payload: dict[str, object] = {
        **fields,
        "identity": str(identity),
        "permissions": list(permissions) if isinstance(permissions, (list, tuple)) else [],
        "mda_user_id": user_id,
    }
    return cast("Mapping[str, object]", _freeze(payload))


def build_principal_envelope(
    configurable: Mapping[str, object] | None,
    agent_id: str = "",
) -> PrincipalEnvelope | None:
    """Project trusted auth stamps into an Agent Auth-aligned principal envelope.

    Returns ``None`` when no caller resolved. Today this is built from
    ``langgraph_auth_user``; later Agent Auth ``resolve-principal`` can supply
    the same shape directly.
    """
    user = (configurable or {}).get("langgraph_auth_user")
    studio = is_studio_auth_user(user)
    user_id = _runtime_user_id(user)
    if user is None or not user_id:
        return None
    is_service = _field(user, "mda_user_kind") == "service" or (
        studio and not _has_verified_studio_identity(user)
    )
    kind = "service" if is_service else "person"
    authority = "agent" if is_service else "user"
    subject: dict[str, object] = {"authority": authority}
    if agent_id:
        subject["agent_id"] = agent_id
    if authority == "user":
        subject["user_id"] = str(user_id)
    envelope: dict[str, object] = {
        "principal": {
            "id": str(user_id),
            "kind": kind,
            "claims": _principal_claims(user),
        },
        "subject": subject,
        "link": {"status": "not_required"},
    }
    return cast("PrincipalEnvelope", _freeze(envelope))


def _principal_claims(user: object) -> dict[str, object]:
    """Build the caller's claim bag, normalizing the two claims MDA names.

    ``mda_groups`` / ``mda_user_email`` are already normalized, but they are
    only stamped when the deployment mapped those claims — mapping both is
    optional. The raw token payload sits in ``mda_claims`` and can carry a
    ``groups`` or ``email`` of any shape, so normalize whatever survives and
    drop it when it does not normalize. Otherwise a deployment that never
    mapped ``groups`` would hand authors the delimited string the IdP sent
    under a key documented as a sequence.
    """
    claims: dict[str, object] = {}
    raw_claims = _field(user, "mda_claims")
    if isinstance(raw_claims, Mapping):
        claims.update(dict(cast("Mapping[str, object]", raw_claims)))

    groups = normalize_groups(_field(user, "mda_groups") or claims.get("groups"))
    if groups:
        claims["groups"] = groups
    else:
        claims.pop("groups", None)

    email = _field(user, "mda_user_email") or claims.get("email")
    if isinstance(email, str) and email:
        claims["email"] = email
    else:
        claims.pop("email", None)

    return claims


def build_managed_server_info(
    runtime: RuntimeConfigLike | object | None,
    configurable: Mapping[str, object] | None,
) -> ManagedServerInfo | None:
    """Preserve native server metadata and add trusted managed identity.

    Empty IDs mean that the platform did not supply an ID. An existing native
    user remains available when no managed auth user is present.
    """
    user = resolve_auth_user(configurable)
    existing = _existing_server_info(runtime)
    if user is None and existing is None:
        return None
    assistant_id = _server_info_str(existing, "assistant_id", "assistantId") or ""
    graph_id = _server_info_str(existing, "graph_id", "graphId") or ""
    envelope = build_principal_envelope(configurable, assistant_id or graph_id)
    return ManagedServerInfo(
        assistant_id=assistant_id,
        graph_id=graph_id,
        user=_AuthUserView(user)
        if user is not None
        else cast("BaseUser | None", _field(existing, "user")),
        principal=cast("PrincipalView", _envelope_view(envelope["principal"]))
        if envelope
        else None,
        subject=cast("SubjectView", _envelope_view(envelope["subject"], "agent_id", "user_id"))
        if envelope
        else None,
        link=cast("LinkView", _envelope_view(envelope["link"], "from_", "to"))
        if envelope
        else None,
        source=build_identity_source(configurable) if user is not None else None,
    )


def build_identity_source(configurable: Mapping[str, object] | None) -> SourceView:
    """Resolve the ingress source for ``server_info.source``.

    Saves authors from reading ``server_info.user["mda_source_provider"]``, the
    loose transport stamp the principal fields exist to replace.
    """
    user = (configurable or {}).get("langgraph_auth_user")
    provider = "studio" if is_studio_auth_user(user) else _source_provider(configurable)
    return cast(
        "SourceView",
        SimpleNamespace(provider=provider, thread_id=_thread_id(configurable)),
    )


def principal_envelope_from_server_info(info: object | None) -> PrincipalEnvelope | None:
    """Reassemble the Agent Auth wire envelope from flattened ``server_info`` fields."""
    if info is None:
        return None
    principal = _attr(info, "principal")
    subject = _attr(info, "subject")
    link = _attr(info, "link")
    if principal is None or subject is None or link is None:
        return None
    return cast(
        "PrincipalEnvelope",
        _freeze(
            {
                "principal": _to_mapping(principal),
                "subject": _to_mapping(subject),
                "link": _to_mapping(link),
            }
        ),
    )


def _attr(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _to_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if hasattr(value, "__dict__"):
        # ``None`` here means the view filled an absent optional key; the wire
        # envelope omits those rather than sending an explicit null.
        return {
            ("from" if str(key) == "from_" else str(key)): item
            for key, item in vars(value).items()
            if not str(key).startswith("_") and item is not None
        }
    return {}


#: Envelope fields that stay mappings in the runtime view. ``claims`` is an
#: opaque IdP bag whose keys are often not valid Python identifiers (namespaced
#: OIDC claims such as ``https://acme.com/tenant``), so authors subscript it —
#: matching ``Record<string, unknown>`` on the TypeScript side.
_MAPPING_VALUED_FIELDS = frozenset({"claims"})


def _envelope_view(value: Mapping[str, object], *optional: str) -> object:
    """Attribute view of one envelope field, with absent optionals set to ``None``.

    The wire envelope omits optional keys (a service principal has no
    ``subject.user_id``; a ``not_required`` link has no ``from``/``to``), but the
    ``*View`` protocols promise ``str | None``. Filling them keeps type-correct
    reads from raising ``AttributeError``; :func:`_to_mapping` drops them again
    so the reassembled wire shape still omits rather than nulls them.
    """
    view = _mapping_to_ns(value)
    for name in optional:
        if not hasattr(view, name):
            setattr(view, name, None)
    return view


def _mapping_to_ns(value: object) -> object:
    """Recursively expose frozen mappings as attribute-accessible namespaces.

    Lets authors write ``runtime.server_info.principal.id`` (parity with
    TypeScript) while the underlying values stay immutable. Keys in
    :data:`_MAPPING_VALUED_FIELDS` keep their mapping so they stay subscriptable.
    """
    if isinstance(value, Mapping):
        return SimpleNamespace(
            **{
                ("from_" if key == "from" else str(key)): (
                    item if key in _MAPPING_VALUED_FIELDS else _mapping_to_ns(item)
                )
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return [_mapping_to_ns(item) for item in value]
    return value


def _existing_server_info(runtime: RuntimeConfigLike | object | None) -> object | None:
    if runtime is None:
        return None
    if isinstance(runtime, Mapping):
        return runtime.get("server_info") or runtime.get("serverInfo")
    info = getattr(runtime, "server_info", None)
    if info is not None:
        return info
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping):
        return config.get("server_info") or config.get("serverInfo")
    return getattr(config, "server_info", None) if config is not None else None


def _server_info_str(existing: object | None, *names: str) -> str | None:
    if existing is None:
        return None
    for name in names:
        if isinstance(existing, Mapping) and name in existing:
            value = existing[name]
        else:
            value = getattr(existing, name, None)
        if isinstance(value, str):
            return value
    return None


class _AuthUserView(Mapping[str, object]):
    """Frozen auth user supporting both attribute and mapping access.

    Satisfies LangGraph's ``BaseUser`` protocol (used by ``ServerInfo.user``)
    while keeping ``mda_*`` claim access via attributes or ``[]``.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, object]) -> None:
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> object:
        data = object.__getattribute__(self, "_data")
        try:
            return data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def identity(self) -> str:
        data = object.__getattribute__(self, "_data")
        value = data.get("identity")
        return str(value) if value is not None else ""

    @property
    def permissions(self) -> Sequence[str]:
        data = object.__getattribute__(self, "_data")
        value = data.get("permissions")
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return []

    @property
    def is_authenticated(self) -> bool:
        data = object.__getattribute__(self, "_data")
        value = data.get("is_authenticated")
        return True if value is None else bool(value)

    @property
    def display_name(self) -> str:
        data = object.__getattribute__(self, "_data")
        value = data.get("display_name")
        if value is not None and str(value).strip():
            return str(value)
        return self.identity

    def __getitem__(self, key: str) -> object:
        return object.__getattribute__(self, "_data")[key]

    def __contains__(self, key: object) -> bool:
        # Not positional-only: LangGraph ``BaseUser`` requires keyword-capable key.
        return key in object.__getattribute__(self, "_data")

    def __iter__(self) -> Iterator[str]:
        return iter(object.__getattribute__(self, "_data"))

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_data"))


def build_runtime_identity(configurable: Mapping[str, object] | None) -> RuntimeIdentity | None:
    """Build the frozen identity envelope from the trusted ``langgraph_auth_user``.

    Returns ``None`` when there is no resolvable actor (identity is opt-in). The
    returned mapping (and its nested mappings) is read-only.

    LangGraph Studio injects a ``StudioUser`` (no ``mda_user_id``) when
    ``x-auth-scheme: langsmith`` is present. A verified LangSmith user maps to a
    person; the local placeholder and callers without a verified user ID remain
    services. Both retain ``source.provider: "studio"``.
    """
    user = (configurable or {}).get("langgraph_auth_user")
    studio = is_studio_auth_user(user)
    user_id = _runtime_user_id(user)
    if not user or not user_id:
        return None
    is_service = _field(user, "mda_user_kind") == "service" or (
        studio and not _has_verified_studio_identity(user)
    )
    caller: RuntimeUser = {
        "kind": "service" if is_service else "person",
        "id": str(user_id),
    }
    email = _field(user, "mda_user_email")
    if email:
        caller["email"] = str(email)
    source: RuntimeSource = {"provider": "studio" if studio else _source_provider(configurable)}
    thread = _thread_id(configurable)
    if thread is not None:
        source["thread_id"] = thread
    identity: RuntimeIdentity = {"user": caller, "source": source}
    groups = _field(user, "mda_groups")
    if isinstance(groups, (list, tuple)) and groups:
        identity["groups"] = tuple(str(group) for group in groups)
    claims = _field(user, "mda_claims")
    if isinstance(claims, Mapping):
        identity["claims"] = dict(cast("Mapping[str, object]", claims))
    _stamp_agent_auth_identity(identity, user)
    if studio and not is_service:
        identity["identity_provider"] = "langsmith"
        identity["identity_subject"] = user_id
    # Immutable: tools/middleware get a read-only view they cannot tamper with.
    return cast("RuntimeIdentity", _freeze(identity))


def _stamp_agent_auth_identity(identity: RuntimeIdentity, user: object) -> None:
    """Copy validated-token / channel Agent Auth stamps onto the envelope."""
    provider = _field(user, "mda_identity_provider")
    if isinstance(provider, str) and provider.strip():
        identity["identity_provider"] = provider.strip()
    issuer = _field(user, "mda_identity_issuer")
    if isinstance(issuer, str) and issuer.strip():
        identity["identity_issuer"] = issuer.strip()
    subject = _field(user, "mda_identity_subject")
    if isinstance(subject, str) and subject.strip():
        identity["identity_subject"] = subject.strip()
    principal_id = _field(user, "mda_agent_auth_principal_id")
    if isinstance(principal_id, str) and principal_id.strip():
        identity["agent_auth_principal_id"] = principal_id.strip()


def _has_verified_studio_identity(user: object) -> bool:
    """Require the Agent Server's verified user ID, not just a Studio marker."""
    ls_user_id = _field(user, "ls_user_id")
    return (
        _field(user, "is_authenticated") is True
        and isinstance(ls_user_id, str)
        and bool(ls_user_id.strip())
    )


def _runtime_user_id(user: object) -> str | None:
    """Resolve the caller ID used by runtime identity and thread ownership."""
    if is_studio_auth_user(user):
        return (
            str(_field(user, "ls_user_id")).strip()
            if _has_verified_studio_identity(user)
            else "langgraph-studio-user"
        )
    user_id = _field(user, "mda_user_id")
    return str(user_id) if user_id else None


def is_studio_auth_user(user: object) -> bool:
    """True when LangGraph Platform / Studio supplied the auth user.

    The Agent Server injects ``langgraph_api.auth.studio_user.StudioUser``,
    which duck-types as a mapping (``get`` / ``keys`` / ``items`` over its
    ``dict()``) but is not a registered ``collections.abc.Mapping``. An
    ``isinstance`` gate therefore rejects the real principal, so read the
    signal through ``_field`` and prefer the SDK's own ``isinstance`` check.

    ``kind`` is the only dependable marker. ``langgraph_api`` builds the
    principal as ``StudioUser(auth_dict["user_id"])`` on the LangSmith backend,
    so ``identity`` carries a real workspace user id there; the literal
    ``langgraph-studio-user`` appears only on the custom-auth path.
    """
    if user is None:
        return False
    # The SDK check is an ``isinstance`` / ``kind`` probe that accepts any value;
    # its signature names the user shapes the Agent Server hands over.
    if is_studio_user(cast("BaseUser", user)):
        return True
    if _field(user, "kind") == "StudioUser":
        return True
    return _field(user, "identity") == "langgraph-studio-user"


def _freeze(value: object, memo: dict[int, object] | None = None) -> object:
    """Recursively wrap mappings/lists so the identity envelope is read-only.

    Shared children are frozen once and reused, so nested mappings stay the
    *same* frozen object when referenced from multiple parents — matching the
    TypeScript envelope, where ``Object.freeze`` preserves identity.
    """
    memo = {} if memo is None else memo
    cached = memo.get(id(value))
    if cached is not None:
        return cached
    frozen: object
    if isinstance(value, Mapping):
        frozen = MappingProxyType({key: _freeze(item, memo) for key, item in value.items()})
    elif isinstance(value, list):
        frozen = tuple(_freeze(item, memo) for item in value)
    else:
        return value
    # Safe against id() reuse: every original stays referenced by the caller for
    # the duration of the walk.
    memo[id(value)] = frozen
    return frozen


def resolve_runtime_configurable(
    runtime: RuntimeConfigLike | object | None,
) -> Mapping[str, object] | None:
    """Resolve ``configurable`` from ``ToolRuntime.config`` or a flat runtime bag.

    When the caller only passes ``{"state": ...}`` (or omits auth), fall back to
    the ambient LangGraph runnable config so ``langgraph_auth_user`` still reaches
    tools and middleware.

    Also fall back when a runtime ``config`` is present but its ``configurable``
    bag lacks ``langgraph_auth_user`` (empty or partial). Returning that bag
    would skip ambient and leave the first turn without a resolved caller identity.
    """
    from_runtime = _configurable_from_runtime(runtime)
    if from_runtime is not None and "langgraph_auth_user" in from_runtime:
        return from_runtime
    ambient = _ambient_configurable()
    if ambient is not None:
        return ambient
    return from_runtime


def _configurable_from_mapping(
    bag: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Read ``configurable`` from a Mapping bag (flat or nested ``config``)."""
    cfg = bag.get("configurable")
    if isinstance(cfg, Mapping):
        return cast("Mapping[str, object]", cfg)
    nested_config = bag.get("config")
    if isinstance(nested_config, Mapping):
        nested = nested_config.get("configurable")
        if isinstance(nested, Mapping):
            return cast("Mapping[str, object]", nested)
    return None


def _configurable_from_runtime(
    runtime: RuntimeConfigLike | object | None,
) -> Mapping[str, object] | None:
    """Read ``configurable`` from a flat runtime, nested config, or Mapping bag."""
    if runtime is None:
        return None
    # Plain Mapping bags (unit tests / duck-typed hooks) use keys, not attrs.
    if isinstance(runtime, Mapping):
        return _configurable_from_mapping(cast("Mapping[str, object]", runtime))
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping) and isinstance(config.get("configurable"), Mapping):
        return cast("Mapping[str, object]", config["configurable"])
    configurable = getattr(runtime, "configurable", None)
    if isinstance(configurable, Mapping):
        return cast("Mapping[str, object]", configurable)
    return None


def _ambient_configurable() -> Mapping[str, object] | None:
    """``configurable`` from the running LangGraph node, if any."""
    try:
        config = get_config()
    except RuntimeError:
        # Called outside a runnable context (e.g. authoring-time tests).
        return None
    if isinstance(config, Mapping):
        nested = config.get("configurable")
        if isinstance(nested, Mapping):
            return cast("Mapping[str, object]", nested)
    return None


def sanitize_configurable(configurable: Mapping[str, object] | None) -> JsonObject:
    """Strip client-supplied identity keys so a body can't spoof the actor.

    The platform-set ``langgraph_auth_user`` is preserved — it is the only trusted
    identity source.
    """
    if not configurable:
        return {}
    return {
        key: value for key, value in configurable.items() if key not in RESERVED_CONFIGURABLE_KEYS
    }


_RUNTIME_SOURCE_PROVIDERS = frozenset({"http", "schedule", "studio", "cli"})


def _is_accepted_runtime_source_provider(provider: str) -> bool:
    if provider in _RUNTIME_SOURCE_PROVIDERS:
        return True
    # Lazy: avoid import cycle with channels package at module load.
    from managed_deepagents._channels.source_providers import (  # noqa: PLC0415
        is_trusted_source_provider,
    )

    return is_trusted_source_provider(provider)


def _source_provider(configurable: Mapping[str, object] | None) -> SourceProvider:
    user = (configurable or {}).get("langgraph_auth_user")
    from_user = _field(user, "mda_source_provider")
    if isinstance(from_user, str) and from_user.strip():
        provider = from_user.strip()
        return provider if _is_accepted_runtime_source_provider(provider) else "http"
    # Legacy top-level stamp (pre-channel); reserved and stripped from clients.
    declared = (configurable or {}).get("mda_source_provider")
    if isinstance(declared, str) and declared.strip():
        provider = declared.strip()
        return provider if _is_accepted_runtime_source_provider(provider) else "http"
    return "http"


def _thread_id(configurable: Mapping[str, object] | None) -> str | None:
    user = (configurable or {}).get("langgraph_auth_user")
    from_user = _field(user, "mda_source_thread_id")
    if from_user is not None and str(from_user):
        return str(from_user)
    # Non-channel / backward-compatible fallback.
    value = (configurable or {}).get("thread_id")
    return None if value is None else str(value)


def _auth_user_fields(user: object) -> dict[str, object] | None:
    """Materialize an auth user's claims as a plain dict.

    LangGraph normalizes a handler's dict into a ``ProxyUser`` (``BaseUser``)
    before it reaches ``configurable["langgraph_auth_user"]``, so the trusted
    caller is usually not a ``Mapping`` at runtime.
    """
    if isinstance(user, Mapping):
        return dict(user)
    if user is None:
        return None
    for name in ("model_dump", "dict"):
        method = getattr(user, name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except TypeError:
            continue
        if isinstance(dumped, Mapping):
            return dict(dumped)
    items = getattr(user, "items", None)
    if callable(items):
        try:
            return dict(items())
        except (TypeError, ValueError):
            return None
    return None


def _field(user: object, key: str) -> object | None:
    if isinstance(user, Mapping):
        return user.get(key)
    getter = getattr(user, "get", None)
    if callable(getter):
        return getter(key)
    return getattr(user, key, None)
