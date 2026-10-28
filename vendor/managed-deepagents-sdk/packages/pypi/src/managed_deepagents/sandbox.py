"""The managed ``sandbox/`` declaration contract.

Mirrors the TypeScript surface: a project calls ``define_sandbox`` and MDA owns
resolution, per-thread reuse, deploy-time snapshot bake, and lifecycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal, TypedDict

from deepagents.backends.protocol import SandboxBackendProtocol

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
        "name",
        "image",
        "snapshot",
        "image_name",
        "template_name",
    )

    #: Options that shape how MDA resolves the box rather than the create request.
    RESOLUTION_OPTION_KEYS: ClassVar[tuple[str, ...]] = (
        "_openswe_per_thread",
        "_openswe_thread_id",
        "_openswe_create_fields",
        "_openswe_no_manage_proxy",
        "_openswe_select",
        "_openswe_without_box",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"SandboxDefinition(options={sorted(self.options)})"


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

    _validate_thread_id(normalized.get("_openswe_thread_id"))
    _validate_create_fields(normalized.get("_openswe_create_fields"))
    if normalized.get("_openswe_no_manage_proxy") and normalized.get("proxy_config") is not None:
        raise TypeError(
            "proxy_config needs MDA to manage the proxy. Remove _openswe_no_manage_proxy."
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
    return normalized


#: Create-request fields MDA sets itself, so ``_openswe_create_fields`` cannot override them.
_RESERVED_CREATE_FIELDS = frozenset(
    {"name", "snapshot", "snapshot_id", "snapshot_name", "wait_for_ready", "timeout"}
)


@dataclass(frozen=True)
class SandboxReplacement:
    """A thread's box replaced by a fresh one under its name; the old box is kept, renamed."""

    old_name: str
    new_name: str
    old_stopped: bool
    stop_error: str | None = None


async def open_sandbox(backend: object) -> str:
    """Adopt or create the run's managed sandbox now and return its name.

    ``backend`` is ``runtime.backend``. The box is otherwise opened on first use.
    """
    from managed_deepagents.runtime import _managed_sandbox_of  # noqa: PLC0415

    return await _managed_sandbox_of(backend).aopen()


async def replace_sandbox(
    backend: object, sandbox: SandboxDefinition | None = None
) -> SandboxReplacement:
    """Give the run's thread a fresh managed sandbox under the same name.

    The fresh box is created from ``sandbox``'s options, the run's own by default.
    The old box is stopped and kept, renamed, until its lifecycle deletes it.
    ``backend`` is ``runtime.backend``.
    """
    from managed_deepagents.runtime import _managed_sandbox_of  # noqa: PLC0415

    return await _managed_sandbox_of(backend).areplace(sandbox)


def owning_thread(options: Mapping[str, object]) -> str | None:
    """The thread whose box a definition uses, when not the run's own."""
    thread_id = options.get("_openswe_thread_id")
    return thread_id if isinstance(thread_id, str) else None


def selected_for(sandbox: SandboxDefinition, runtime: object) -> SandboxDefinition | None:
    """The box ``runtime``'s run uses: what ``_openswe_select`` picks for it, else ``sandbox``.

    None when the run has no box.
    """
    select = sandbox.options.get("_openswe_select")
    if not callable(select):
        return sandbox
    selected = select(runtime)
    if selected is not None and not isinstance(selected, SandboxDefinition):
        raise TypeError("_openswe_select must return define_sandbox(...) or None")
    return selected


def without_box(sandbox: SandboxDefinition) -> SandboxBackendProtocol | None:
    """Where a run ``_openswe_select`` picks no box for acts instead, if anywhere."""
    backend = sandbox.options.get("_openswe_without_box")
    return backend if isinstance(backend, SandboxBackendProtocol) else None


def is_per_thread(options: Mapping[str, object]) -> bool:
    """Whether the thread keeps one box whatever a dynamic selection's options."""
    return options.get("_openswe_per_thread") is True


def declared_create_fields(options: Mapping[str, object]) -> dict[str, object]:
    """Create-request fields sent as-is, beyond the ones the SDK models."""
    fields = options.get("_openswe_create_fields")
    return dict(fields) if isinstance(fields, dict) else {}


def manages_proxy(options: Mapping[str, object]) -> bool:
    """Whether MDA sets the box's proxy config before each command."""
    return options.get("_openswe_no_manage_proxy") is not True


def _validate_thread_id(thread_id: object) -> None:
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id.strip()):
        raise TypeError("sandbox _openswe_thread_id must be a non-empty string")


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
    "SandboxReplacement",
    "basic",
    "bearer",
    "normalize_sandbox_options",
    "open_sandbox",
    "replace_sandbox",
]
