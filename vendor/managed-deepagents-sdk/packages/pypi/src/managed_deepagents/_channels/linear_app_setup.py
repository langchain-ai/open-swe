from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, NotRequired, TypedDict

from managed_deepagents._channel_icon import validate_channel_icon_path
from managed_deepagents._channels.http.provider import defined_fields, handled_event_keys

if TYPE_CHECKING:
    from managed_deepagents._channels.http.types import JsonValue
    from managed_deepagents._channels.linear_types import LinearAppOptions

# The webhook resource type that delivers each event.
EVENT_RESOURCE_TYPES: dict[str, str] = {
    "Issue.create": "Issue",
    "Issue.update": "Issue",
    "Comment.create": "Comment",
    "AgentSessionEvent.created": "AgentSessionEvent",
    "AgentSessionEvent.prompted": "AgentSessionEvent",
}

_SESSION_EVENTS = ["AgentSessionEvent.created", "AgentSessionEvent.prompted"]
# A revoked install, so the runtime can say why events stopped.
_ALWAYS_SUBSCRIBED = ["OAuthAuthorization"]
# Reading the workspace, and writing comments and agent activities.
_BASE_SCOPES = ["read", "write"]
# Mentioning the app and delegating issues to it, which open agent sessions.
_SESSION_SCOPES = ["app:mentionable", "app:assignable"]
# Scopes Linear can grant along with `write` without being asked. Agent Auth
# refuses a grant beyond the allowed scopes, so they are allowed.
_IMPLIED_SCOPES = ["customer:read", "customer:write", "initiative:read", "initiative:write"]

_APP_OPTIONS = ("name", "description", "icon", "teams")
_APP_NAME_MIN_CHARS = 2
_APP_NAME_MAX_CHARS = 80
_APP_DESCRIPTION_MAX_CHARS = 1000
_MAX_TEAMS = 100
_TEAM_KEY = re.compile(r"[A-Za-z0-9]{1,16}")
_FORBIDDEN_IN_NAME = re.compile(r"linear|https?://", re.IGNORECASE)


class LinearAppSetup(TypedDict):
    """What ``mda deploy`` needs to set up the Linear app."""

    events: list[str]
    """The handled event keys, such as ``Issue.create``."""
    sessions: bool
    """Whether the app answers agent sessions."""
    webhookResourceTypes: list[str]
    """The webhook resource types the app subscribes to."""
    scopes: list[str]
    """The OAuth scopes the install asks for."""
    allowedScopes: list[str]
    """The most the install may be granted."""
    app: NotRequired[dict[str, JsonValue]]
    """How the app looks; deploy names it after the agent when unset."""


def linear_app_setup(
    handlers: Mapping[str, object], app: LinearAppOptions | None, *, sessions: bool
) -> LinearAppSetup:
    """The app the channel's handlers and options need.

    The CLI reads it from the channel's manifest instead of working it out;
    the TypeScript runtime mirrors these rules in ``linear/app-setup.ts``.
    """
    if app is not None:
        _check_app_options(app)
    events = sorted(
        key
        for key in {*handled_event_keys(handlers), *(_SESSION_EVENTS if sessions else [])}
        if key in EVENT_RESOURCE_TYPES
    )
    # A session event handler answers sessions even with `sessions=False`.
    sessions = any(key in _SESSION_EVENTS for key in events)
    resource_types = {*_ALWAYS_SUBSCRIBED, *(EVENT_RESOURCE_TYPES[key] for key in events)}
    if sessions:
        # A change to the app's team access changes which sessions reach it.
        resource_types.add("PermissionChange")
    scopes = [*_BASE_SCOPES, *(_SESSION_SCOPES if sessions else [])]
    setup = LinearAppSetup(
        events=events,
        sessions=sessions,
        webhookResourceTypes=sorted(resource_types),
        scopes=scopes,
        allowedScopes=[*scopes, *_IMPLIED_SCOPES],
    )
    if app is not None:
        name = app.get("name")
        description = app.get("description")
        setup["app"] = defined_fields(
            {
                "name": name.strip() if name is not None else None,
                "description": description.strip() if description is not None else None,
                "icon": validate_channel_icon_path(app["icon"], "Linear app.icon")
                if "icon" in app
                else None,
            }
        )
    return setup


def _check_app_options(app: LinearAppOptions) -> None:
    """Check the app options against Linear's rules before deploy sends them."""
    # A misspelled option would silently widen what starts runs.
    unknown = sorted(set(app) - set(_APP_OPTIONS))
    if unknown:
        raise ValueError(
            f"unknown Linear app option {', '.join(unknown)}; use {', '.join(_APP_OPTIONS)}"
        )
    name = app.get("name")
    if name is not None and (
        not _APP_NAME_MIN_CHARS <= len(name.strip()) <= _APP_NAME_MAX_CHARS
        or _FORBIDDEN_IN_NAME.search(name)
    ):
        raise ValueError('Linear app.name must be 2-80 characters, without "Linear" or a URL')
    description = app.get("description")
    if description is not None and not 0 < len(description.strip()) <= _APP_DESCRIPTION_MAX_CHARS:
        raise ValueError("Linear app.description must be 1-1000 characters")
    teams = app.get("teams")
    if teams is None:
        return
    if not teams:
        raise ValueError("Linear app.teams must list team keys; omit it to allow every team")
    if len(teams) > _MAX_TEAMS:
        raise ValueError(f"Linear app.teams must list at most {_MAX_TEAMS} teams")
    for team in teams:
        if not _TEAM_KEY.fullmatch(team.strip()):
            raise ValueError(
                f"Linear app.teams has an invalid team key '{team.strip()}'; use keys such as ENG"
            )
