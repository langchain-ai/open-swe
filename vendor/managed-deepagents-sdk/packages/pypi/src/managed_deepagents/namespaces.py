"""The first-party primitive namespaces.

Provider collections use ``<primitive>.<provider>(...)`` namespaces, while
resources use ``define_*`` factories::

    channels.slack(name="Support Bot", description="Answers support questions")
    define_mcp(servers={...})
    define_sandbox(default_timeout=600)
    auth.supabase(project_ref="abc")

These are the only primitives the managed runtime will mount. A project cannot
contribute its own — :mod:`managed_deepagents._extension` stays internal.
"""

from __future__ import annotations

import warnings

from pydantic import BaseModel, ConfigDict, field_validator
from typing_extensions import Unpack

from managed_deepagents._channels.http import http_channel
from managed_deepagents._channels.trigger.channel import slack_channel
from managed_deepagents._connectors.mcp import McpServersConfig, McpServersDefinition, define_mcp
from managed_deepagents.identity import auth
from managed_deepagents.sandbox import ProxyConfig, SandboxDefinition, normalize_sandbox_options

__all__ = ["auth", "channels", "connectors", "define_mcp", "define_sandbox"]

_CONNECTORS_MCP_DEPRECATION = (
    "connectors.mcp(...) is deprecated; use define_mcp(...) instead. It will be removed in 0.8.0."
)


class _Namespace:
    """A namespace whose providers are attributes, e.g. ``channels.slack``.

    Subclasses set the providers as class attributes wrapped in
    :func:`staticmethod`, matching how the TypeScript namespaces are plain
    objects of factories.
    """

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        providers = ", ".join(sorted(k for k in vars(type(self)) if not k.startswith("_")))
        return f"{type(self).__name__.lstrip('_').lower()}({providers})"


class _Channels(_Namespace):
    """Inbound messaging surfaces (Events ingress + outbound messaging).

    Slack ingress through Trigger Server.
    """

    slack = staticmethod(slack_channel)
    http = staticmethod(http_channel)


def _deprecated_mcp(**kwargs: Unpack[McpServersConfig]) -> McpServersDefinition:
    warnings.warn(_CONNECTORS_MCP_DEPRECATION, FutureWarning, stacklevel=2)
    return define_mcp(**kwargs)


class _Connectors(_Namespace):
    """Deprecated MCP namespace. Use :func:`define_mcp` instead.

    ``connectors.mcp`` will be removed in 0.8.0.
    """

    mcp = staticmethod(_deprecated_mcp)


class _SandboxRegistryArgs(BaseModel):
    """Private registry declaration reconciled by MDA during bake."""

    url: str
    username: str
    password_env: str

    @field_validator("url", "username", "password_env")
    @classmethod
    def reject_blank_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value


class _LangSmithSandboxArgs(BaseModel):
    """The LangSmith sandbox's options.

    ``idle_ttl_seconds`` is the managed lifecycle knob; bake bases
    (``snapshot_name`` / ``snapshot_id`` / ``docker_image``) select the deploy
    bake starting point. ``registry`` declares private pull credentials by
    environment-variable name; MDA creates or updates the Host registry.
    Unmodeled keys pass through as LangSmith create options. MDA owns naming,
    lifecycle, ``setup.sh`` bake, and cleanup — the envelope rejects the keys
    it owns. Reuse is one box per thread and selection; ``per_thread=True``
    keeps one box per thread whatever a dynamic selection's options, which then
    apply only when MDA creates it. ``thread_id`` uses that thread's box instead
    of the run's own. ``create_fields`` go into the create request as-is, for
    fields the SDK does not model. ``manage_proxy=False`` leaves the box's proxy
    config to whoever else sets it.
    """

    model_config = ConfigDict(extra="allow")

    per_thread: bool | None = None
    thread_id: str | None = None
    create_fields: dict[str, object] | None = None
    manage_proxy: bool | None = None
    idle_ttl_seconds: int | None = None
    api_key: str | None = None
    default_timeout: int | None = None
    snapshot_name: str | None = None
    snapshot_id: str | None = None
    docker_image: str | None = None
    registry: _SandboxRegistryArgs | None = None


def define_sandbox(
    *,
    per_thread: bool | None = None,
    thread_id: str | None = None,
    create_fields: dict[str, object] | None = None,
    manage_proxy: bool | None = None,
    idle_ttl_seconds: int | None = None,
    api_key: str | None = None,
    default_timeout: int | None = None,
    snapshot_name: str | None = None,
    snapshot_id: str | None = None,
    docker_image: str | None = None,
    registry: dict[str, str] | None = None,
    proxy_config: ProxyConfig | dict[str, object] | None = None,
    **options: object,
) -> SandboxDefinition:
    """Declare the managed execution environment for an agent."""
    parsed = _LangSmithSandboxArgs.model_validate(
        {
            **options,
            "per_thread": per_thread,
            "thread_id": thread_id,
            "create_fields": create_fields,
            "manage_proxy": manage_proxy,
            "idle_ttl_seconds": idle_ttl_seconds,
            "api_key": api_key,
            "default_timeout": default_timeout,
            "snapshot_name": snapshot_name,
            "snapshot_id": snapshot_id,
            "docker_image": docker_image,
            "registry": registry,
        }
    )
    dumped = parsed.model_dump(exclude_none=True)
    # Pydantic serializes helper dataclasses; the unresolved template must retain them.
    if proxy_config is not None:
        dumped["proxy_config"] = proxy_config
    normalized = normalize_sandbox_options(dumped)
    return SandboxDefinition(normalized)


channels = _Channels()
connectors = _Connectors()
