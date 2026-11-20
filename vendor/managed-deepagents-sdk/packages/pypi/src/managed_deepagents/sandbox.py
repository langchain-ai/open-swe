"""The managed ``sandbox/`` declaration contract.

Mirrors the TypeScript surface: a project calls ``define_sandbox`` and MDA owns
resolution, per-thread reuse, deploy-time snapshot bake, and lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict

from managed_deepagents._connections import ConnectionReference, is_connection_reference

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import ManagedServerRuntime


@dataclass(frozen=True, repr=False)
class ConnectionHeader:
    """A connection value formatted when a sandbox command starts."""

    connection: ConnectionReference
    format: Literal["bearer", "basic", "template"]
    username: str | None = None
    template: str | None = None

    def __post_init__(self) -> None:
        if not is_connection_reference(self.connection):
            raise TypeError("sandbox header requires a connection reference")
        if self.format not in {"bearer", "basic", "template"}:
            raise ValueError("sandbox header format must be bearer, basic or template")
        if self.format == "template" and not _is_credential_template(self.template):
            raise ValueError(
                "credential template must contain {token} and no other braces, "
                'for example "token {token}"'
            )
        if self.format == "basic" and (
            not isinstance(self.username, str) or not self.username.strip() or ":" in self.username
        ):
            raise ValueError("basic username must be a non-empty string without ':'")


def bearer(connection: ConnectionReference) -> ConnectionHeader:
    return ConnectionHeader(connection, "bearer")


def basic(username: str, connection: ConnectionReference) -> ConnectionHeader:
    return ConnectionHeader(connection, "basic", username)


def credential_template(template: str, connection: ConnectionReference) -> ConnectionHeader:
    """Write a connection's credential into a header value, in your own wording.

    ``connections.get(...)`` returns a reference, not the credential: the token
    is fetched for the current caller right before each sandbox command, and
    may be refreshed between commands. It can't be put into a string when
    ``sandbox/`` loads; ``f"token {ref}"`` would write the reference, not the
    token. This records the wording now and fills in the credential wherever
    ``{token}`` appears when the command runs.

    ``bearer(ref)`` is the same as ``credential_template("Bearer {token}", ref)``.
    Use ``basic()`` for Basic auth, which needs encoding.
    """
    return ConnectionHeader(connection, "template", template=template)


def _is_credential_template(template: object) -> bool:
    if not isinstance(template, str) or "{token}" not in template:
        return False
    rest = template.replace("{token}", "")
    return "{" not in rest and "}" not in rest


class ProxyHeader(TypedDict, total=False):
    name: str
    value: str | ConnectionReference | ConnectionHeader
    type: str


class ProxyRule(TypedDict, total=False):
    """Common header-rule fields; provider rules also accept plain dictionaries."""

    type: str
    name: str
    match_hosts: list[str]
    headers: list[ProxyHeader]
    enabled: bool


class ProxyAccessControl(TypedDict, total=False):
    allow_list: list[str]
    deny_list: list[str]


class ProxyConfig(TypedDict, total=False):
    rules: list[ProxyRule | dict[str, object]]
    no_proxy: list[str]
    access_control: ProxyAccessControl


SandboxResolve = Callable[["ManagedServerRuntime"], "SandboxDefinition | None"]
"""Choose a run's sandbox from its runtime; ``None`` runs it without a sandbox."""


@dataclass(frozen=True)
class SandboxDefinition:
    """The pre-runtime sandbox spec exported from ``sandbox/__init__.py``.

    A Managed Deep Agent declares its execution environment with
    ``sandbox = define_sandbox(...)``. MDA owns how it is *resolved*:
    construction, per-thread reuse, deploy-time ``setup.sh`` bake, and
    lifecycle/TTL.

    ``resolve`` is called once per run, at the run's first sandbox use, and kept
    out of ``options`` so the options stay plain data.
    """

    options: dict[str, object]
    resolve: SandboxResolve | None = None
    kind: ClassVar[str] = "sandbox"

    #: Option keys MDA owns; a project must not set these.
    MANAGED_OPTION_KEYS: ClassVar[tuple[str, ...]] = (
        "name",
        "image",
        "snapshot",
        "image_name",
        "template_name",
    )

    #: Options that shape how MDA resolves the sandbox rather than the create request.
    RESOLUTION_OPTION_KEYS: ClassVar[tuple[str, ...]] = (
        "_openswe_sandbox_id",
        "_openswe_create_fields",
        "_openswe_no_manage_proxy",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        resolve = ", resolve=..." if self.resolve is not None else ""
        return f"SandboxDefinition(options={sorted(self.options)}{resolve})"


def normalize_sandbox_options(options: dict[str, object]) -> dict[str, object]:
    """Reject provider options MDA owns and enforce bake-base mutual exclusion.

    Used by :func:`define_sandbox`, so runtime-owned keys never enter a
    definition. A fixed definition reuses one sandbox per thread; a dynamic
    entrypoint may select a different definition for a run.
    """
    if "scope" in options:
        raise TypeError(
            "sandbox scope was removed: sandbox reuse is always one box per thread. "
            "Remove scope from the sandbox declaration."
        )

    normalized = {**options}

    managed = [key for key in SandboxDefinition.MANAGED_OPTION_KEYS if key in normalized]
    if managed:
        raise TypeError(
            f"{', '.join(managed)} {'is' if len(managed) == 1 else 'are'} owned by "
            "the managed runtime and cannot be set on a sandbox. MDA supplies the "
            "run-scoped name automatically."
        )

    bases = [
        name for name in ("snapshot_name", "snapshot_id", "docker_image") if normalized.get(name)
    ]
    if len(bases) > 1:
        raise TypeError(f"{', '.join(bases)} are mutually exclusive. Pass only one bake base.")

    if normalized.get("registry") and not normalized.get("docker_image"):
        raise TypeError("registry requires docker_image. Pass both when pulling a private image.")

    from managed_deepagents._sandbox_proxy import (  # noqa: PLC0415
        validate_and_collect_sandbox_proxy_config_references,
    )

    validate_and_collect_sandbox_proxy_config_references(normalized.get("proxy_config"))
    _validate_create_fields(normalized.get("_openswe_create_fields"))
    if normalized.get("_openswe_no_manage_proxy") and normalized.get("proxy_config") is not None:
        raise TypeError(
            "proxy_config needs MDA to manage the proxy. Remove _openswe_no_manage_proxy."
        )
    return normalized


class SandboxMissingError(RuntimeError):
    """The thread's sandbox is gone, so MDA fails the run instead of replacing it.

    The thread's metadata binds it to the sandbox. Either LangSmith confirms the
    sandbox no longer exists, or another sandbox now holds its name
    (``replaced``). The thread's work lived in that sandbox, so MDA never starts
    over in an empty one.
    """

    def __init__(self, name: str | None, *, replaced: bool = False) -> None:
        self.name = name
        self.replaced = replaced
        super().__init__(
            f"Sandbox {name!r} was replaced by another sandbox, so this thread's workspace is gone"
            if replaced
            else f"Sandbox {name!r} no longer exists, so this thread's workspace is gone"
        )


#: Create-request fields MDA sets itself, so ``_openswe_create_fields`` cannot override them.
_RESERVED_CREATE_FIELDS = frozenset(
    {"name", "snapshot", "snapshot_id", "snapshot_name", "wait_for_ready", "timeout"}
)


def declared_create_fields(options: Mapping[str, object]) -> dict[str, object]:
    """Create-request fields sent as-is, beyond the ones the SDK models."""
    fields = options.get("_openswe_create_fields")
    return dict(fields) if isinstance(fields, dict) else {}


def manages_proxy(options: Mapping[str, object]) -> bool:
    """Whether MDA sets the sandbox's proxy config before each command."""
    return options.get("_openswe_no_manage_proxy") is not True


def _validate_create_fields(fields: object) -> None:
    if fields is None:
        return
    if not isinstance(fields, dict) or not all(isinstance(key, str) for key in fields):
        raise TypeError("_openswe_create_fields must map field names to values")
    reserved = sorted(_RESERVED_CREATE_FIELDS.intersection(fields))
    if reserved:
        raise TypeError(f"_openswe_create_fields cannot set {', '.join(reserved)}; MDA sets them")


__all__ = [
    "ConnectionHeader",
    "ProxyAccessControl",
    "ProxyConfig",
    "ProxyHeader",
    "ProxyRule",
    "SandboxDefinition",
    "SandboxMissingError",
    "SandboxResolve",
    "basic",
    "bearer",
    "normalize_sandbox_options",
]
