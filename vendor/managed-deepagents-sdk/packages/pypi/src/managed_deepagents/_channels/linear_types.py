"""Handwritten webhook subsets; other provider fields remain in the original payload."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from typing_extensions import TypedDict, TypeVar

from managed_deepagents._channels.http.endpoint import EndpointEvent, EndpointRequest, EventEndpoint
from managed_deepagents._channels.linear_schema import (
    LinearCommentCreatePayload,
    LinearEventKey,
    LinearIssueCreatePayload,
    LinearIssueUpdatePayload,
    LinearSessionCreatedPayload,
    LinearSessionPromptedPayload,
)
from managed_deepagents._channels.run_input import ChannelRunResult

if TYPE_CHECKING:
    from managed_deepagents._channels.http.types import ChannelName


@dataclass(frozen=True)
class LinearTeam:
    id: str
    key: str


@dataclass(frozen=True)
class LinearActor:
    id: str
    type: str | None = None
    name: str | None = None


@dataclass(frozen=True, kw_only=True)
class _LinearEventFields:
    delivery_id: str
    organization_id: str
    issue_id: str
    """The issue the event is about; its conversation's thread."""
    team: LinearTeam | None = None
    actor: LinearActor | None = None
    """Who acted: the webhook's actor, or for a session the user who started it
    or replied in it."""


@dataclass(frozen=True, kw_only=True)
class LinearIssueCreateEvent(_LinearEventFields):
    key: Literal["Issue.create"] = "Issue.create"
    type: Literal["Issue"] = "Issue"
    action: Literal["create"] = "create"
    payload: LinearIssueCreatePayload


@dataclass(frozen=True, kw_only=True)
class LinearIssueUpdateEvent(_LinearEventFields):
    key: Literal["Issue.update"] = "Issue.update"
    type: Literal["Issue"] = "Issue"
    action: Literal["update"] = "update"
    payload: LinearIssueUpdatePayload


@dataclass(frozen=True, kw_only=True)
class LinearCommentCreateEvent(_LinearEventFields):
    key: Literal["Comment.create"] = "Comment.create"
    type: Literal["Comment"] = "Comment"
    action: Literal["create"] = "create"
    payload: LinearCommentCreatePayload


@dataclass(frozen=True, kw_only=True)
class LinearSessionCreatedEvent(_LinearEventFields):
    key: Literal["AgentSessionEvent.created"] = "AgentSessionEvent.created"
    type: Literal["AgentSessionEvent"] = "AgentSessionEvent"
    action: Literal["created"] = "created"
    payload: LinearSessionCreatedPayload
    session_id: str
    """The agent session; its conversation's thread."""


@dataclass(frozen=True, kw_only=True)
class LinearSessionPromptedEvent(_LinearEventFields):
    key: Literal["AgentSessionEvent.prompted"] = "AgentSessionEvent.prompted"
    type: Literal["AgentSessionEvent"] = "AgentSessionEvent"
    action: Literal["prompted"] = "prompted"
    payload: LinearSessionPromptedPayload
    session_id: str
    """The agent session; its conversation's thread."""


LinearSessionEvent = LinearSessionCreatedEvent | LinearSessionPromptedEvent
"""An agent session event."""
LinearEvent = (
    LinearIssueCreateEvent
    | LinearIssueUpdateEvent
    | LinearCommentCreateEvent
    | LinearSessionCreatedEvent
    | LinearSessionPromptedEvent
)
"""A Linear event; its ``key`` decides its ``payload``'s type."""

_Event = TypeVar("_Event", bound=LinearEvent, default=LinearEvent)

LinearEndpointRequest = (
    EndpointRequest[EndpointEvent[Literal["Issue.create"], LinearIssueCreatePayload]]
    | EndpointRequest[EndpointEvent[Literal["Issue.update"], LinearIssueUpdatePayload]]
    | EndpointRequest[EndpointEvent[Literal["Comment.create"], LinearCommentCreatePayload]]
    | EndpointRequest[
        EndpointEvent[Literal["AgentSessionEvent.created"], LinearSessionCreatedPayload]
    ]
    | EndpointRequest[
        EndpointEvent[Literal["AgentSessionEvent.prompted"], LinearSessionPromptedPayload]
    ]
)
"""What a Linear channel POSTs an endpoint: its event key picks the payload's type,
so one receiver validates every key it is sent with
``TypeAdapter(LinearEndpointRequest)``."""


@dataclass(frozen=True)
class LinearChannelThread:
    """The conversation's thread an event belongs to.

    A handler runs before any run exists, so this is not a run's runtime.
    """

    id: str
    """The conversation's thread, the issue's or an agent session's, which the
    run the handler starts runs on. Useful for run context that names the
    thread, such as a token a backend signs for it."""
    channel: ChannelName


class LinearAppOptions(TypedDict, total=False):
    """The Linear app ``mda deploy`` sets up: how it looks, and what it may do."""

    name: str
    """The app's name, shown where people mention it and in its comments: 2-80
    characters, without "Linear" or a URL. Defaults to the agent's name.

    Applies only when deploy creates the app. Linear has no API to change an
    app, so changing it later does nothing: rename an existing app on its
    settings page in Linear. Deploy reminds you when it keeps an existing app."""
    description: str
    """What the app does, shown when a workspace admin installs it: up to 1,000
    characters. Like ``name``, it applies only when deploy creates the app;
    change an existing app's description on its settings page in Linear."""
    icon: str
    """The app's icon: a PNG or JPEG image, as a path relative to ``channels/``
    like the Slack channel's ``icon``, such as ``assets/icon.png``. At least
    256x256 pixels and at most 1 MB. The deployment serves it at
    ``/channels/{name}/icon``, and deploy points the new app's icon there. Like
    ``name``, it applies only when deploy creates the app."""
    teams: list[str]
    """Keys of the teams (as in ``ENG-123``) the channel is limited to: events
    about other teams' issues start nothing. Defaults to every team the app can
    see. The app's token still reaches every team it was granted, so limit its
    team access in Linear too."""


LinearEventResult = ChannelRunResult | None
"""What an event handler returns: a run's input, as ``agent.invoke`` takes it — a
string, a message such as ``HumanMessage(...)``, state such as
``{"messages": [...]}``, or ``(input, {"context": ..., "metadata": ...})``. ``None``
starts nothing."""

LinearEventCallback = Callable[
    [_Event, LinearChannelThread],
    LinearEventResult | Awaitable[LinearEventResult],
]

LinearHandlers = TypedDict(
    "LinearHandlers",
    {
        "Issue.create": LinearEventCallback[LinearIssueCreateEvent] | EventEndpoint,
        "Issue.update": LinearEventCallback[LinearIssueUpdateEvent] | EventEndpoint,
        "Comment.create": LinearEventCallback[LinearCommentCreateEvent] | EventEndpoint,
        "AgentSessionEvent.created": LinearEventCallback[LinearSessionCreatedEvent] | EventEndpoint,
        "AgentSessionEvent.prompted": LinearEventCallback[LinearSessionPromptedEvent]
        | EventEndpoint,
    },
    total=False,
)
"""Event handlers that start runs. Return a prompt (string, message, or state),
or ``None`` to skip. The run's final reply is posted as a comment on the issue,
under the comment that started it.

Example::

    {"Issue.create": lambda e, _: f"Triage {e.payload['data']['identifier']}"}
"""


__all__ = [
    "LinearActor",
    "LinearAppOptions",
    "LinearChannelThread",
    "LinearCommentCreateEvent",
    "LinearEndpointRequest",
    "LinearEvent",
    "LinearEventCallback",
    "LinearEventKey",
    "LinearEventResult",
    "LinearHandlers",
    "LinearIssueCreateEvent",
    "LinearIssueUpdateEvent",
    "LinearSessionCreatedEvent",
    "LinearSessionEvent",
    "LinearSessionPromptedEvent",
    "LinearTeam",
]
