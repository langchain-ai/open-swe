"""The first-party primitive namespaces.

Provider collections use ``<primitive>.<provider>(...)`` namespaces, while
resources use ``define_*`` factories::

    channels.slack(app={"name": "Support Bot", "description": "Answers support questions"})
    define_mcp(servers={...})
    define_sandbox(default_timeout=600)
    auth.supabase(project_ref="abc")

These are the only primitives the managed runtime will mount. A project cannot
contribute its own — :mod:`managed_deepagents._extension` stays internal.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from managed_deepagents._channels.github import github_channel
from managed_deepagents._channels.http import http_channel
from managed_deepagents._channels.linear import linear_channel
from managed_deepagents._channels.trigger.channel import slack_channel
from managed_deepagents._connectors.mcp import define_mcp
from managed_deepagents.identity import auth
from managed_deepagents.sandbox import (
    ProxyConfig,
    SandboxDefinition,
    SandboxResolve,
    normalize_sandbox_options,
)

__all__ = ["auth", "channels", "define_mcp", "define_sandbox"]


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

    Slack ingress through Trigger Server. ``github`` and ``linear`` are
    experimental and may change in a minor release.
    """

    github = staticmethod(github_channel)
    linear = staticmethod(linear_channel)
    slack = staticmethod(slack_channel)
    http = staticmethod(http_channel)


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
    it owns. Reuse is always one box per thread.
    """

    model_config = ConfigDict(extra="allow")

    idle_ttl_seconds: int | None = None
    api_key: str | None = None
    default_timeout: int | None = None
    snapshot_name: str | None = None
    snapshot_id: str | None = None
    docker_image: str | None = None
    registry: _SandboxRegistryArgs | None = None


def define_sandbox(
    *,
    idle_ttl_seconds: int | None = None,
    api_key: str | None = None,
    default_timeout: int | None = None,
    snapshot_name: str | None = None,
    snapshot_id: str | None = None,
    docker_image: str | None = None,
    registry: dict[str, str] | None = None,
    proxy_config: ProxyConfig | dict[str, object] | None = None,
    resolve: SandboxResolve | None = None,
    existing_sandbox: str | None = None,
    **options: object,
) -> SandboxDefinition:
    """Declare the managed execution environment for an agent.

    ``resolve(runtime)`` picks each run's sandbox, once per run, from the runtime
    the agent factory receives. Its definition overrides these options for that
    run; ``None`` runs it without a sandbox.

    ``existing_sandbox`` names a LangSmith sandbox, in the deployment's workspace,
    that the thread opens instead of the one MDA names for it. MDA binds the thread
    to it but never creates it: a missing one fails the run with
    ``SandboxMissingError``. Its proxy config is left alone unless the run declares a
    ``proxy_config``, and a config one run wrote stays through later runs that declare
    none. Set it on the definition ``resolve`` returns, on every
    run of the thread; a run that names none opens MDA's own sandbox for the thread.
    It can name any sandbox the deployment's key reaches, another thread's included,
    so take the name from something the agent trusts, never from a caller's input.
    """
    if resolve is not None and not callable(resolve):
        raise TypeError("sandbox resolve must be a function")
    parsed = _LangSmithSandboxArgs.model_validate(
        {
            **options,
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
    if existing_sandbox is not None:
        dumped["existing_sandbox"] = existing_sandbox
    normalized = normalize_sandbox_options(dumped)
    return SandboxDefinition(normalized, resolve)


channels = _Channels()
