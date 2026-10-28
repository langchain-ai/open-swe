"""Who caused a channel event.

The signed sender of a GitHub or Linear webhook, or the Slack user Trigger
Server names on a delivery, which MDA can't verify yet. It is personal data:
MDA neither logs it nor adds it to a run's metadata. Kept free of the agent
stack and PyJWT, so the package can name these types without loading either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, overload

from typing_extensions import TypedDict


@dataclass(frozen=True, kw_only=True)
class SlackActor:
    """A Slack user, as Trigger Server read them from Slack's signed request.

    MDA doesn't yet verify the delivery's service key, so this is no identity
    check."""

    provider: Literal["slack"] = "slack"
    id: str
    """The Slack user ID."""
    team_id: str
    """Their own Slack workspace (team). In a Slack Connect channel, someone
    from another organization keeps theirs rather than your app's."""


@dataclass(frozen=True, kw_only=True)
class GitHubActor:
    """A GitHub account, as the signed webhook's ``sender`` names it; ``name`` is its login."""

    provider: Literal["github"] = "github"
    id: str
    name: str
    type: Literal["User", "Bot", "Organization"]
    email: str | None = None


@dataclass(frozen=True)
class LinearActor:
    """A Linear user or app, as the signed webhook names it."""

    id: str
    type: str | None = None
    """Linear's kind of actor, such as ``user``, when it sends one."""
    name: str | None = None
    email: str | None = None
    provider: Literal["linear"] = "linear"


ChannelActor = SlackActor | GitHubActor | LinearActor
"""Who caused an event, on any channel; switch on ``provider``."""


class SlackEndpointActor(TypedDict):
    """A ``SlackActor`` as an endpoint is sent it, in camelCase as TypeScript's."""

    provider: Literal["slack"]
    id: str
    teamId: str
    """Their own Slack workspace (team)."""


class GitHubEndpointActor(TypedDict):
    """A ``GitHubActor`` as an endpoint is sent it."""

    provider: Literal["github"]
    id: str
    name: str
    type: Literal["User", "Bot", "Organization"]
    email: NotRequired[str]


class LinearEndpointActor(TypedDict):
    """A ``LinearActor`` as an endpoint is sent it."""

    provider: Literal["linear"]
    id: str
    type: NotRequired[str]
    """Linear's kind of actor, such as ``user``, when it sends one."""
    name: NotRequired[str]
    email: NotRequired[str]


EndpointActor: TypeAlias = SlackEndpointActor | GitHubEndpointActor | LinearEndpointActor
"""A ``ChannelActor`` as an endpoint is sent it; switch on ``provider``."""


@overload
def endpoint_actor(actor: SlackActor) -> SlackEndpointActor: ...
@overload
def endpoint_actor(actor: GitHubActor) -> GitHubEndpointActor: ...
@overload
def endpoint_actor(actor: LinearActor) -> LinearEndpointActor: ...
def endpoint_actor(actor: ChannelActor) -> EndpointActor:
    """An actor as an endpoint is sent it: its set fields, in camelCase."""
    if isinstance(actor, GitHubActor):
        github: GitHubEndpointActor = {
            "provider": "github",
            "id": actor.id,
            "name": actor.name,
            "type": actor.type,
        }
        if actor.email is not None:
            github["email"] = actor.email
        return github
    if isinstance(actor, SlackActor):
        return {"provider": "slack", "id": actor.id, "teamId": actor.team_id}
    linear: LinearEndpointActor = {"provider": "linear", "id": actor.id}
    if actor.type is not None:
        linear["type"] = actor.type
    if actor.name is not None:
        linear["name"] = actor.name
    if actor.email is not None:
        linear["email"] = actor.email
    return linear


__all__ = [
    "ChannelActor",
    "EndpointActor",
    "GitHubActor",
    "GitHubEndpointActor",
    "LinearActor",
    "LinearEndpointActor",
    "SlackActor",
    "SlackEndpointActor",
    "endpoint_actor",
]
