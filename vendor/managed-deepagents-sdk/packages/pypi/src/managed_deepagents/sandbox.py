"""The managed ``sandbox/`` declaration contract.

Mirrors the TypeScript surface: a project calls ``define_sandbox`` and MDA owns
resolution, per-thread reuse, deploy-time snapshot bake, and lifecycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal, TypedDict

from managed_deepagents._connections import ConnectionReference, is_connection_reference


@dataclass(frozen=True, repr=False)
class ConnectionHeader:
    """A connection value formatted when a sandbox command starts."""

    connection: ConnectionReference
    format: Literal["bearer", "basic"]
    username: str | None = None

    def __post_init__(self) -> None:
        if not is_connection_reference(self.connection):
            raise TypeError("sandbox header requires a connection reference")
        if self.format not in {"bearer", "basic"}:
            raise ValueError("sandbox header format must be bearer or basic")
        if self.format == "basic" and (
            not isinstance(self.username, str) or not self.username.strip() or ":" in self.username
        ):
            raise ValueError("basic username must be a non-empty string without ':'")


def bearer(connection: ConnectionReference) -> ConnectionHeader:
    return ConnectionHeader(connection, "bearer")


def basic(username: str, connection: ConnectionReference) -> ConnectionHeader:
    return ConnectionHeader(connection, "basic", username)


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


@dataclass(frozen=True)
class SandboxDefinition:
    """The pre-runtime sandbox spec exported from ``sandbox/__init__.py``.

    A Managed Deep Agent declares its execution environment with
    ``sandbox = define_sandbox(...)``. MDA owns how it is *resolved*:
    construction, per-thread reuse, deploy-time ``setup.sh`` bake, and
    lifecycle/TTL.
    """

    options: dict[str, object]
    kind: ClassVar[str] = "sandbox"

    #: Option keys MDA owns; a project must not set these.
    MANAGED_OPTION_KEYS: ClassVar[tuple[str, ...]] = (
        "image",
        "snapshot",
        "image_name",
        "template_name",
        "scope",
    )

    #: Options that shape how MDA resolves the box rather than the create request.
    RESOLUTION_OPTION_KEYS: ClassVar[tuple[str, ...]] = ("name", "create_fields", "manage_proxy")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"SandboxDefinition(options={sorted(self.options)})"


def normalize_sandbox_options(options: dict[str, object]) -> dict[str, object]:
    """Reject provider options MDA owns and enforce bake-base mutual exclusion.

    Used by :func:`define_sandbox`, so runtime-owned keys never enter a
    definition. A fixed definition reuses one sandbox per thread; a dynamic
    entrypoint may select a different definition for a run.
    """
    if "scope" in options and options["scope"] != "thread":
        raise TypeError(
            'sandbox scope is managed as "thread". Remove scope or pass the legacy value "thread".'
        )

    normalized = {**options}
    normalized.pop("scope", None)

    managed = [key for key in SandboxDefinition.MANAGED_OPTION_KEYS if key in normalized]
    if managed:
        raise TypeError(
            f"{', '.join(managed)} {'is' if len(managed) == 1 else 'are'} owned by "
            "the managed runtime and cannot be set on a sandbox. MDA supplies the "
            "run-scoped name automatically."
        )

    _validate_box_name(normalized.get("name"))
    _validate_create_fields(normalized.get("create_fields"))
    if normalized.get("manage_proxy") is False and normalized.get("proxy_config") is not None:
        raise TypeError("proxy_config needs MDA to manage the proxy. Remove manage_proxy=False.")

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
    return normalized


#: Host's sandbox-name limit.
_MAX_BOX_NAME_LENGTH = 63
#: Create-request fields MDA sets itself, so ``create_fields`` cannot override them.
_RESERVED_CREATE_FIELDS = frozenset(
    {"name", "snapshot", "snapshot_id", "snapshot_name", "wait_for_ready", "timeout"}
)


def declared_box_name(options: Mapping[str, object]) -> str | None:
    """The box a definition names, shared by every run that declares it."""
    name = options.get("name")
    return name if isinstance(name, str) else None


def declared_create_fields(options: Mapping[str, object]) -> dict[str, object]:
    """Create-request fields sent as-is, beyond the ones the SDK models."""
    fields = options.get("create_fields")
    return dict(fields) if isinstance(fields, dict) else {}


def manages_proxy(options: Mapping[str, object]) -> bool:
    """Whether MDA sets the box's proxy config before each command."""
    return options.get("manage_proxy") is not False


def _validate_box_name(name: object) -> None:
    if name is None:
        return
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or len(name) > _MAX_BOX_NAME_LENGTH
    ):
        raise TypeError(
            f"sandbox name must be a non-empty string of at most {_MAX_BOX_NAME_LENGTH} "
            "characters without surrounding whitespace"
        )


def _validate_create_fields(fields: object) -> None:
    if fields is None:
        return
    if not isinstance(fields, dict) or not all(isinstance(key, str) for key in fields):
        raise TypeError("create_fields must be a mapping of field names to values")
    reserved = sorted(_RESERVED_CREATE_FIELDS.intersection(fields))
    if reserved:
        raise TypeError(f"create_fields cannot set {', '.join(reserved)}; MDA sets them")


__all__ = [
    "ConnectionHeader",
    "ProxyAccessControl",
    "ProxyConfig",
    "ProxyHeader",
    "ProxyRule",
    "SandboxDefinition",
    "basic",
    "bearer",
    "normalize_sandbox_options",
]
