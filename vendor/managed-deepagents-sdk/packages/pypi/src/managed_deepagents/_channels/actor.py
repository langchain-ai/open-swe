"""Who caused a channel event, as its provider verified it.

The signed sender of a GitHub or Linear webhook, or the Slack user Trigger
Server signs into a delivery. It is personal data: MDA neither logs it nor adds
it to a run's metadata. Kept free of the agent stack and PyJWT, so the package
can name these types without loading either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypeAlias, overload

from typing_extensions import TypedDict


@dataclass(frozen=True, kw_only=True)
class SlackActor:
    """A Slack user, as Trigger Server verified and signed them."""

    provider: Literal["slack"] = "slack"
    id: str
    """The Slack user ID."""
    team_id: str
    """The Slack workspace (team)."""
    enterprise_id: str | None = None
    """The Enterprise Grid organization, when the workspace is in one."""
    name: str | None = None
    email: str | None = None
    """The user's email, when the Slack app may read it."""


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
    """The Slack workspace (team)."""
    enterpriseId: NotRequired[str]
    """The Enterprise Grid organization, when the workspace is in one."""
    name: NotRequired[str]
    email: NotRequired[str]


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
    sent: SlackEndpointActor | LinearEndpointActor
    if isinstance(actor, SlackActor):
        sent = {"provider": "slack", "id": actor.id, "teamId": actor.team_id}
        if actor.enterprise_id is not None:
            sent["enterpriseId"] = actor.enterprise_id
    else:
        sent = {"provider": "linear", "id": actor.id}
        if actor.type is not None:
            sent["type"] = actor.type
    if actor.name is not None:
        sent["name"] = actor.name
    if actor.email is not None:
        sent["email"] = actor.email
    return sent


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
