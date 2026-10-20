"""Agent connection names reserved by channels, and the connections holding them."""

from __future__ import annotations

from collections.abc import Callable

from managed_deepagents._channels.github_connections import (
    GITHUB_TOKEN_SLUG,
    github_app_connection_slug,
)
from managed_deepagents._channels.linear_connections import (
    LINEAR_TOKEN_SLUG,
    linear_app_connection_slug,
)

# First-party channels' reserved names, listed here so resolution does not
# depend on adapter modules being loaded.
_BUILTIN_RESERVED_CONNECTIONS: dict[str, Callable[[str], str]] = {
    GITHUB_TOKEN_SLUG: github_app_connection_slug,
    LINEAR_TOKEN_SLUG: linear_app_connection_slug,
}
# What each reserved token is, and what the agent acts as with it.
_RESERVED_TOKEN_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    GITHUB_TOKEN_SLUG: ("the GitHub App's installation token", "act on GitHub as the App"),
    LINEAR_TOKEN_SLUG: ("the Linear app's token", "act in Linear as the app"),
}


def reserved_user_connection_error(slug: str) -> str | None:
    """Why a channel's reserved agent connection cannot be a user connection.

    ``None`` for any other slug. Reading an app's token as the person running
    the agent is not supported yet.
    """
    described = _RESERVED_TOKEN_DESCRIPTIONS.get(slug)
    if described is None:
        return None
    token, acts_as = described
    return (
        f"{slug!r} is not supported as a user connection yet: it is {token}, an agent "
        f'connection. Use connections.get({slug!r}, {{"type": "agent"}}) to {acts_as}.'
    )


def reserved_agent_connection_slug(slug: str, deployment_id: str | None) -> str:
    """The connection that actually holds a channel's reserved agent connection.

    Other slugs pass through unchanged.
    """
    resolve = _BUILTIN_RESERVED_CONNECTIONS.get(slug)
    if resolve is not None and deployment_id:
        return resolve(deployment_id)
    return slug
