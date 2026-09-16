"""Short-TTL reads of one workspace's workspace settings.

Graph factories read these on every run, so they are cached. The cache lives in
a module-level dict shared by every run in the process, which is why the slug is
part of every key: one worker serves every workspace, and the workspace that
populated a key first must not answer for the others.

Callers pass the run's slug explicitly. A factory runs outside the graph's
context, where :func:`agent.dashboard.workspace_settings.resolve_settings_workspace`
cannot see a ``configurable`` and would silently answer ``default``.
"""

from typing import Any, Literal

from agent.dashboard.workspace_settings import (
    get_effective_gateway_enabled,
    get_org_review_guidelines,
    get_workspace_agent_routing_models,
    get_workspace_default_model,
    get_workspace_default_model_pair,
    get_workspace_default_thread_title_model,
    get_workspace_fable_enabled,
    get_workspace_model_routing_enabled,
    get_workspace_settings,
    resolve_settings_workspace,
)
from agent.utils import ttl_cache

SETTINGS_TTL_SECONDS = 60
# Guidelines are long, rarely edited, and read once per review.
GUIDELINES_TTL_SECONDS = 300


def _key(prefix: str, workspace: str) -> str:
    return f"{prefix}:{workspace}"


async def cached_workspace_settings(workspace: str | None) -> dict[str, Any]:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("team:settings", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_settings(slug),
    )


async def cached_workspace_default_model_pair(
    kind: Literal["agent", "reviewer"], workspace: str | None
) -> tuple[tuple[str, str], tuple[str, str]]:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key(f"team-default-model-pair:{kind}", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_default_model_pair(kind, slug),
    )


async def cached_workspace_default_model(
    role: Literal["agent", "reviewer", "chat"], workspace: str | None
) -> tuple[str, str]:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key(f"team-default-model:{role}", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_default_model(role, slug),
    )


async def cached_agent_routing_models(workspace: str | None) -> dict[str, tuple[str, str]]:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("team:agent-routing-models", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_agent_routing_models(slug),
    )


async def cached_thread_title_model(workspace: str | None) -> tuple[str, str]:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("team:thread-title-model", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_default_thread_title_model(slug),
    )


async def cached_gateway_enabled(workspace: str | None) -> bool:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("team:gateway-enabled", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_effective_gateway_enabled(slug),
    )


async def cached_fable_enabled(workspace: str | None) -> bool:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("team:fable-enabled", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_fable_enabled(slug),
    )


async def cached_model_routing_enabled(workspace: str | None) -> bool:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("team:model-routing-enabled", slug),
        SETTINGS_TTL_SECONDS,
        lambda: get_workspace_model_routing_enabled(slug),
    )


async def cached_org_review_guidelines(workspace: str | None) -> str | None:
    slug = resolve_settings_workspace(workspace)
    return await ttl_cache.cached(
        _key("reviewer:org-guidelines", slug),
        GUIDELINES_TTL_SECONDS,
        lambda: get_org_review_guidelines(slug),
    )
