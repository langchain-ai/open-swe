"""The managed connector protocol (Python mirror of the TS contract).

A connector is one integration with one external platform. It may contribute
tools, HTTP routes, sandbox provisioning, inbound events, and/or a named
messaging surface. Discovery is file-based: the CLI globs modules under
``connectors/`` that define a module-level ``connector`` attribute, imports
each one, and hands them to :func:`collect_connectors`, which stamps each with
its file-stem ``name``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal, TypedDict

__all__ = [
    "CONNECTOR_BRAND",
    "Connector",
    "ConnectorRequirements",
    "EventsContext",
    "SandboxContext",
    "collect_connectors",
    "has_sandbox_connectors",
    "is_connector",
    "load_connector_tools",
    "requirements_from_connectors",
    "run_connector_sandbox_hooks",
]

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol
    from langchain_core.runnables import RunnableConfig

    from managed_deepagents._channels.manifest import ChannelManifest
    from managed_deepagents._channels.runtime import ChannelMessagingAdapter
    from managed_deepagents.define_deep_agent import DeepAgentTool
else:
    DeepAgentTool = object
    ChannelManifest = Mapping[str, object]
    ChannelMessagingAdapter = object

#: Empty context bag passed to connector ``tools`` hooks (TS ``Record<string, never>``).
ToolContext = Mapping[str, object]
ConnectorToolList = list[DeepAgentTool]

#: Deploy/runtime requirements a connector declares (former channel manifest).
ConnectorRequirements = ChannelManifest

#: Attribute marking a value as a managed connector. Set on the base class so
#: every subclass inherits it; :func:`is_connector` checks for it truthily.
CONNECTOR_BRAND = "__mda_connector__"

SandboxMode = Literal["create", "reuse", "credentials"]

# Keep in sync with CLI ``plugin_module_extensions`` (ts/tsx/mts/cts) plus
# emitted js/mjs and Python sources that may appear in diagnostics paths.
_STEM_RE = re.compile(r"^(?:.*/)?([^/]+)\.(?:ts|tsx|mts|cts|js|mjs|py)$")


class _SandboxContext:
    """Concrete sandbox context passed to connector hooks."""

    backend: SandboxBackendProtocol
    mode: SandboxMode
    config: RunnableConfig | None
    identity: object | None

    def __init__(
        self,
        *,
        backend: SandboxBackendProtocol,
        mode: SandboxMode,
        config: RunnableConfig | None = None,
        identity: object | None = None,
    ) -> None:
        self.backend = backend
        self.mode = mode
        self.config = config
        self.identity = identity


# Structural alias for type checkers / docs (concrete type is ``_SandboxContext``).
SandboxContext = _SandboxContext


class Connector:
    """Base class for managed connectors.

    Implement at least one of :meth:`tools`, :meth:`http`, :meth:`sandbox`,
    :meth:`events`, :meth:`messaging`, or :meth:`requirements`. A connector that
    implements none does nothing and is rejected by :func:`collect_connectors`.
    Subclasses set a descriptive ``kind``.
    """

    #: Brand inherited by every subclass; do not override.
    __mda_connector__ = True

    #: Diagnostic / provider label (e.g. ``"mcp_servers"``, ``"slack"``).
    #: HTTP mounts use :attr:`name`, not kind.
    kind: str = "connector"

    #: File-stem instance name, stamped by :func:`collect_connectors`.
    name: str | None = None


class EventsContext(TypedDict, total=False):
    """Context handed to connector ``events`` for ``/connectors/{name}/events``."""

    name: str
    request: object
    raw_body: str | bytes
    connector: Connector


def is_connector(value: object) -> bool:
    """Return ``True`` when ``value`` carries the connector brand."""
    return bool(getattr(value, CONNECTOR_BRAND, False))


def _connector_has_hook(connector: object) -> bool:
    return any(
        callable(getattr(connector, hook, None))
        for hook in ("tools", "http", "sandbox", "events", "messaging", "requirements")
    )


def collect_connectors(modules: list[tuple[str, object]]) -> list[Connector]:
    """Validate and collect the connectors discovered under ``connectors/``.

    Each module should expose exactly one connector via a module-level
    ``connector`` attribute. The file stem becomes the instance ``name`` (mount
    namespace and messaging address). Duplicate stems are a hard error. Modules
    missing that attribute, or whose ``connector`` is not a branded connector,
    are skipped. A branded connector that implements no hooks is still a hard
    error. Duplicate connector objects (re-exported from two modules) are
    de-duplicated by identity.

    ``modules`` is a list of ``(source, module)`` pairs where ``source`` is the
    project-relative path used in error messages.
    """
    collected: list[Connector] = []
    seen_objects: set[int] = set()
    seen_names: set[str] = set()

    for source, module in modules:
        candidate = getattr(module, "connector", None)
        if candidate is None or not is_connector(candidate):
            continue
        if not _connector_has_hook(candidate):
            raise ValueError(
                f"{source} connector (kind {candidate.kind!r}) implements none of "
                f"`tools`, `http`, `sandbox`, `events`, `messaging`, or "
                f"`requirements` and would do nothing."
            )
        name = _connector_stem_name(source)
        if name in seen_names:
            raise ValueError(f"duplicate connector name '{name}' from connectors/")
        seen_names.add(name)
        if id(candidate) in seen_objects:
            continue
        seen_objects.add(id(candidate))
        object.__setattr__(candidate, "name", name)
        collected.append(candidate)

    from managed_deepagents._channels.source_providers import (  # noqa: PLC0415
        register_declared_source_providers,
    )

    register_declared_source_providers(collected)
    return collected


def _connector_stem_name(source: str) -> str:
    match = _STEM_RE.match(source.replace("\\", "/"))
    if match is None:
        raise ValueError(f'invalid connector path "{source}"')
    base = match.group(1)
    if not base or base == "connector":
        raise ValueError(
            f'invalid connector path "{source}": use connectors/<name>.py (not connector.py)'
        )
    return base


def load_connector_tools(
    connectors: list[Connector], ctx: ToolContext | None = None
) -> ConnectorToolList:
    """Run every connector's ``tools`` hook and concatenate the results.

    Connectors without a ``tools`` hook (e.g. HTTP-only connectors) contribute
    nothing. Order is preserved.
    """
    tools: ConnectorToolList = []
    for connector in connectors:
        hook = getattr(connector, "tools", None)
        if callable(hook):
            tools.extend(hook(ctx if ctx is not None else {}))
    return tools


def has_sandbox_connectors(connectors: Sequence[object] | None) -> bool:
    """Return ``True`` when at least one connector implements ``sandbox``."""
    return any(callable(getattr(connector, "sandbox", None)) for connector in connectors or ())


def requirements_from_connectors(
    connectors: Sequence[object],
) -> dict[str, ConnectorRequirements]:
    """Build the requirements map (file-stem → manifest) from ``requirements`` hooks."""
    out: dict[str, ConnectorRequirements] = {}
    for connector in connectors:
        hook = getattr(connector, "requirements", None)
        name = getattr(connector, "name", None)
        if not callable(hook) or not isinstance(name, str) or not name:
            continue
        out[name] = hook(name)
    return out


def run_connector_sandbox_hooks(
    connectors: Sequence[object] | None,
    *,
    backend: SandboxBackendProtocol,
    mode: SandboxMode,
    config: RunnableConfig | None = None,
    identity: object | None = None,
) -> None:
    """Run every connector's ``sandbox`` hook, preserving order."""
    ctx = _SandboxContext(backend=backend, mode=mode, config=config, identity=identity)
    for connector in connectors or ():
        hook = getattr(connector, "sandbox", None)
        if callable(hook):
            hook(ctx)
