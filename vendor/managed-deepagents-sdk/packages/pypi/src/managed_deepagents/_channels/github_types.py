"""Handwritten webhook subsets; other provider fields remain in the original payload."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal

from typing_extensions import TypedDict, TypeVar

from managed_deepagents._channels.actor import GitHubActor, GitHubEndpointActor
from managed_deepagents._channels.github_schema import (
    GitHubInstallationEventKey,
    GitHubInstallationPayload,
    GitHubInstallationRepository,
    GitHubPayload,
    SupportedEventKey,
    _CheckRunPayload,
    _CheckSuitePayload,
    _InstallationPayload,
    _InstallationRepositoriesPayload,
    _IssueCommentPayload,
    _IssuePayload,
    _LabeledIssuePayload,
    _LabeledPullRequestPayload,
    _MergeGroupDestroyedPayload,
    _MergeGroupPayload,
    _PullRequestPayload,
    _PushPayload,
    _ReviewCommentPayload,
    _ReviewPayload,
    _StatusPayload,
    _WorkflowPayload,
)
from managed_deepagents._channels.http.endpoint import EndpointRequest, EventEndpoint
from managed_deepagents._channels.run_input import ChannelRunResult

if TYPE_CHECKING:
    from managed_deepagents._channels.http.types import ChannelName

_Payload = TypeVar("_Payload", bound=GitHubPayload, default=GitHubPayload)
_InstallationPayloadT = TypeVar(
    "_InstallationPayloadT", bound=GitHubInstallationPayload, default=GitHubInstallationPayload
)


@dataclass(frozen=True)
class GitHubRepository:
    id: int
    owner: str
    name: str
    full_name: str
    private: bool


@dataclass(frozen=True)
class GitHubSender:
    id: int
    login: str
    type: Literal["User", "Bot", "Organization"]


@dataclass(frozen=True)
class GitHubEvent(Generic[_Payload]):
    key: SupportedEventKey
    name: str
    action: str | None
    """``None`` for events without an action, such as ``push``."""
    delivery_id: str
    installation_id: int
    repository: GitHubRepository
    sender: GitHubSender
    payload: _Payload
    mentions: tuple[str, ...] = ()
    """The handles the text this event wrote mentions, such as ``@octocat`` or
    a text tag like ``@openswe``: lowercased, each once, in order. The text is
    a new or edited comment's or review's body, or an opened or edited issue's
    or pull request's title and body; an edit lists only the handles it added.
    As on GitHub, code, quotes and HTML comments mention no one. Empty for
    events that write no text, such as a push or a label."""
    from_app: bool = False
    """Whether the channel's own App caused the event: its comment, its push,
    a pull request it opened. Such events reach handlers only when the channel
    is created with ``include_own_events=True``; a handler that answers
    comments skips them, so the agent doesn't answer itself."""

    @property
    def actor(self) -> GitHubActor:
        """The ``sender``, in the shape every channel's ``actor`` has."""
        return GitHubActor(id=str(self.sender.id), name=self.sender.login, type=self.sender.type)


@dataclass(frozen=True)
class GitHubAccount:
    """The user or organization an App is installed on."""

    id: int
    login: str | None
    """``None`` for an enterprise account, which has a ``slug`` instead."""


@dataclass(frozen=True)
class GitHubInstallationEvent(Generic[_InstallationPayloadT]):
    """An event about the App's installation rather than a repository: it was
    installed, removed, suspended, unsuspended or given new permissions, or
    repositories were added to it or removed from it.

    GitHub sends these to every App, so handling one subscribes to nothing and
    needs no permission. It belongs to no issue or pull request: each delivery
    gets its own thread, and nothing is posted on GitHub for it.
    """

    key: GitHubInstallationEventKey
    name: str
    action: str
    delivery_id: str
    installation_id: int
    account: GitHubAccount
    sender: GitHubSender
    payload: _InstallationPayloadT

    @property
    def actor(self) -> GitHubActor:
        """The ``sender``, in the shape every channel's ``actor`` has."""
        return GitHubActor(id=str(self.sender.id), name=self.sender.login, type=self.sender.type)


@dataclass(frozen=True)
class GitHubChannelThread:
    """The conversation's thread an event belongs to.

    A handler runs before any run exists, so this is not a run's runtime.
    """

    id: str
    """The thread's ID: the one a run the handler starts runs on, or, when
    ``interrupted``, the one that waits. Useful for run context that names the
    thread, such as a token a backend signs for it."""
    channel: ChannelName
    interrupted: bool = False
    """Set when the thread waits on an interrupt. A run the handler returns
    for this thread doesn't start: it only has the App tell the issue or pull
    request that a decision is pending. Runs on threads of its own choosing
    (``thread``) still start."""


class GitHubAppOptions(TypedDict, total=False):
    """App ``mda deploy`` creates; permissions and repository allowlist.

    Deploy applies changes to these on every run, except to the App's own
    permissions (see ``permissions``).
    """

    permissions: dict[str, Literal["read", "write"]]
    """Permissions the agent's GitHub token needs beyond those the handled
    events need, e.g. ``{"contents": "read"}``. A new App asks for both.

    Handled events set their own level: read to receive an event, write to
    also reply where it happened. These can raise that level but not lower it.

    Deploy renews the agent's token with changed permissions, but it cannot
    change the App itself: GitHub reads an App's permissions from its manifest
    only when the App is created, and has no API to change them later. The
    App's owner grants added permissions, and removes dropped or lowered ones,
    in the App's settings on GitHub. Deploy names both and links there; until
    a dropped permission is removed, the App keeps it even though the agent's
    token no longer has it."""
    repositories: list[str]
    """Repositories (``owner/name``, one owner) the channel is limited to: events
    from other repositories start nothing, and deploy reports listed ones the
    App is not installed on. Defaults to every repository the App is installed
    on. The agent's token still reaches every repository of the installation,
    so install the App only on the ones the agent may change."""


GitHubEventResult = ChannelRunResult | None
"""What an event handler returns: a run's input, as ``agent.invoke`` takes it — a
string, a message such as ``HumanMessage(...)``, state such as
``{"messages": [...]}``, or ``(input, {"context": ..., "metadata": ...})``. ``None``
starts nothing."""

_Key = TypeVar("_Key", bound=str, default=str)
_EventPayload = TypeVar("_EventPayload", default=GitHubPayload)


class _EndpointRepository(TypedDict):
    id: int
    owner: str
    name: str
    fullName: str
    private: bool


class _EndpointAccount(TypedDict):
    id: int
    login: str | None


class _EndpointSender(TypedDict):
    id: int
    login: str
    type: Literal["User", "Bot", "Organization"]


class GitHubEndpointFields(TypedDict, total=False):
    """A GitHub event's fields its endpoints are sent beside its key, delivery ID
    and payload, as a handler gets them on ``GitHubEvent``.

    Each is optional, so a receiver takes requests from older deployments that
    send fewer: read ``mentions`` as ``event.get("mentions", [])``.
    """

    action: str
    """Absent for events without an action, such as ``push``."""
    repository: _EndpointRepository
    """Absent for installation events, which have ``account`` instead."""
    account: _EndpointAccount
    """Installation events only: the account the App is installed on."""
    sender: _EndpointSender
    actor: GitHubEndpointActor
    """As ``GitHubEvent.actor``: the ``sender``, in the shape every channel's actor has."""
    mentions: list[str]
    """As ``GitHubEvent.mentions``: the handles the event's own text mentions."""
    fromApp: bool
    """As ``GitHubEvent.from_app``: whether the channel's own App caused the event."""


class GitHubEndpointEvent(GitHubEndpointFields, Generic[_Key, _EventPayload]):
    """The event a GitHub channel sends its endpoint: ``EndpointEvent``'s key,
    delivery ID and payload, with the fields of ``GitHubEndpointFields``.

    The three are declared here rather than inherited from ``EndpointEvent``:
    pydantic doesn't carry a subclass's type parameters into an inherited
    generic TypedDict, so the key wouldn't pick the payload's type.
    """

    key: _Key
    deliveryId: str
    payload: _EventPayload


GitHubEndpointRequest = (
    EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "issues.opened",
                "issues.edited",
                "issues.reopened",
                "issues.closed",
                "issues.deleted",
                "issues.transferred",
                "issues.pinned",
                "issues.unpinned",
                "issues.assigned",
                "issues.unassigned",
                "issues.locked",
                "issues.unlocked",
                "issues.milestoned",
                "issues.demilestoned",
                "issues.typed",
                "issues.untyped",
            ],
            _IssuePayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "issues.labeled",
                "issues.unlabeled",
            ],
            _LabeledIssuePayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "issue_comment.created",
                "issue_comment.edited",
                "issue_comment.deleted",
            ],
            _IssueCommentPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "pull_request.opened",
                "pull_request.edited",
                "pull_request.reopened",
                "pull_request.ready_for_review",
                "pull_request.converted_to_draft",
                "pull_request.synchronize",
                "pull_request.closed",
                "pull_request.assigned",
                "pull_request.unassigned",
                "pull_request.review_requested",
                "pull_request.review_request_removed",
                "pull_request.locked",
                "pull_request.unlocked",
                "pull_request.milestoned",
                "pull_request.demilestoned",
                "pull_request.enqueued",
                "pull_request.dequeued",
                "pull_request.auto_merge_enabled",
                "pull_request.auto_merge_disabled",
            ],
            _PullRequestPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "pull_request.labeled",
                "pull_request.unlabeled",
            ],
            _LabeledPullRequestPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "pull_request_review.submitted",
                "pull_request_review.edited",
                "pull_request_review.dismissed",
            ],
            _ReviewPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "pull_request_review_comment.created",
                "pull_request_review_comment.edited",
                "pull_request_review_comment.deleted",
            ],
            _ReviewCommentPayload,
        ]
    ]
    | EndpointRequest[GitHubEndpointEvent[Literal["push"], _PushPayload]]
    | EndpointRequest[GitHubEndpointEvent[Literal["status"], _StatusPayload]]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "check_run.completed",
                "check_run.created",
                "check_run.rerequested",
                "check_run.requested_action",
            ],
            _CheckRunPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "check_suite.completed",
                "check_suite.requested",
                "check_suite.rerequested",
            ],
            _CheckSuitePayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "workflow_run.completed",
                "workflow_run.requested",
                "workflow_run.in_progress",
            ],
            _WorkflowPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[Literal["merge_group.checks_requested"], _MergeGroupPayload]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[Literal["merge_group.destroyed"], _MergeGroupDestroyedPayload]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal[
                "installation.created",
                "installation.deleted",
                "installation.suspend",
                "installation.unsuspend",
                "installation.new_permissions_accepted",
            ],
            _InstallationPayload,
        ]
    ]
    | EndpointRequest[
        GitHubEndpointEvent[
            Literal["installation_repositories.added", "installation_repositories.removed"],
            _InstallationRepositoriesPayload,
        ]
    ]
)
"""What a GitHub channel POSTs an endpoint: its event key picks the payload's type,
so one receiver validates every key it is sent with
``TypeAdapter(GitHubEndpointRequest)``."""

GitHubEventCallback = Callable[
    [GitHubEvent[_Payload], GitHubChannelThread],
    GitHubEventResult | Awaitable[GitHubEventResult],
]
GitHubInstallationEventCallback = Callable[
    [GitHubInstallationEvent[_InstallationPayloadT], GitHubChannelThread],
    GitHubEventResult | Awaitable[GitHubEventResult],
]

__all__ = [
    "GitHubAccount",
    "GitHubAppOptions",
    "GitHubChannelThread",
    "GitHubCheckRunPayload",
    "GitHubCheckSuitePayload",
    "GitHubEndpointEvent",
    "GitHubEndpointRequest",
    "GitHubEvent",
    "GitHubEventCallback",
    "GitHubEventResult",
    "GitHubHandlers",
    "GitHubInstallationEvent",
    "GitHubInstallationEventCallback",
    "GitHubInstallationEventKey",
    "GitHubInstallationEventPayload",
    "GitHubInstallationPayload",
    "GitHubInstallationRepositoriesPayload",
    "GitHubInstallationRepository",
    "GitHubIssueCommentPayload",
    "GitHubIssuePayload",
    "GitHubLabeledIssuePayload",
    "GitHubLabeledPullRequestPayload",
    "GitHubMergeGroupDestroyedPayload",
    "GitHubMergeGroupPayload",
    "GitHubPayload",
    "GitHubPullRequestPayload",
    "GitHubPushPayload",
    "GitHubRepository",
    "GitHubReviewCommentPayload",
    "GitHubReviewPayload",
    "GitHubSender",
    "GitHubStatusPayload",
    "GitHubWorkflowPayload",
    "SupportedEventKey",
]

GitHubHandlers = TypedDict(
    "GitHubHandlers",
    {
        "issues.opened": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.edited": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.reopened": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.labeled": GitHubEventCallback[_LabeledIssuePayload] | EventEndpoint,
        "issues.closed": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.deleted": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.transferred": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.pinned": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.unpinned": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.assigned": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.unassigned": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.unlabeled": GitHubEventCallback[_LabeledIssuePayload] | EventEndpoint,
        "issues.locked": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.unlocked": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.milestoned": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.demilestoned": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.typed": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issues.untyped": GitHubEventCallback[_IssuePayload] | EventEndpoint,
        "issue_comment.created": GitHubEventCallback[_IssueCommentPayload] | EventEndpoint,
        "issue_comment.edited": GitHubEventCallback[_IssueCommentPayload] | EventEndpoint,
        "issue_comment.deleted": GitHubEventCallback[_IssueCommentPayload] | EventEndpoint,
        "pull_request.opened": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.edited": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.reopened": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.ready_for_review": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.converted_to_draft": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.synchronize": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.labeled": GitHubEventCallback[_LabeledPullRequestPayload] | EventEndpoint,
        "pull_request.closed": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.assigned": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.unassigned": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.review_requested": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.review_request_removed": GitHubEventCallback[_PullRequestPayload]
        | EventEndpoint,
        "pull_request.unlabeled": GitHubEventCallback[_LabeledPullRequestPayload] | EventEndpoint,
        "pull_request.locked": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.unlocked": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.milestoned": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.demilestoned": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.enqueued": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.dequeued": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.auto_merge_enabled": GitHubEventCallback[_PullRequestPayload] | EventEndpoint,
        "pull_request.auto_merge_disabled": GitHubEventCallback[_PullRequestPayload]
        | EventEndpoint,
        "pull_request_review.submitted": GitHubEventCallback[_ReviewPayload] | EventEndpoint,
        "pull_request_review.edited": GitHubEventCallback[_ReviewPayload] | EventEndpoint,
        "pull_request_review.dismissed": GitHubEventCallback[_ReviewPayload] | EventEndpoint,
        "pull_request_review_comment.created": GitHubEventCallback[_ReviewCommentPayload]
        | EventEndpoint,
        "pull_request_review_comment.edited": GitHubEventCallback[_ReviewCommentPayload]
        | EventEndpoint,
        "pull_request_review_comment.deleted": GitHubEventCallback[_ReviewCommentPayload]
        | EventEndpoint,
        "push": GitHubEventCallback[_PushPayload] | EventEndpoint,
        "status": GitHubEventCallback[_StatusPayload] | EventEndpoint,
        "check_run.completed": GitHubEventCallback[_CheckRunPayload] | EventEndpoint,
        "check_run.created": GitHubEventCallback[_CheckRunPayload] | EventEndpoint,
        "check_run.rerequested": GitHubEventCallback[_CheckRunPayload] | EventEndpoint,
        "check_run.requested_action": GitHubEventCallback[_CheckRunPayload] | EventEndpoint,
        "check_suite.completed": GitHubEventCallback[_CheckSuitePayload] | EventEndpoint,
        "check_suite.requested": GitHubEventCallback[_CheckSuitePayload] | EventEndpoint,
        "check_suite.rerequested": GitHubEventCallback[_CheckSuitePayload] | EventEndpoint,
        "workflow_run.completed": GitHubEventCallback[_WorkflowPayload] | EventEndpoint,
        "workflow_run.requested": GitHubEventCallback[_WorkflowPayload] | EventEndpoint,
        "workflow_run.in_progress": GitHubEventCallback[_WorkflowPayload] | EventEndpoint,
        "merge_group.checks_requested": GitHubEventCallback[_MergeGroupPayload] | EventEndpoint,
        "merge_group.destroyed": GitHubEventCallback[_MergeGroupDestroyedPayload] | EventEndpoint,
        "installation.created": GitHubInstallationEventCallback[_InstallationPayload]
        | EventEndpoint,
        "installation.deleted": GitHubInstallationEventCallback[_InstallationPayload]
        | EventEndpoint,
        "installation.suspend": GitHubInstallationEventCallback[_InstallationPayload]
        | EventEndpoint,
        "installation.unsuspend": GitHubInstallationEventCallback[_InstallationPayload]
        | EventEndpoint,
        "installation.new_permissions_accepted": GitHubInstallationEventCallback[
            _InstallationPayload
        ]
        | EventEndpoint,
        "installation_repositories.added": GitHubInstallationEventCallback[
            _InstallationRepositoriesPayload
        ]
        | EventEndpoint,
        "installation_repositories.removed": GitHubInstallationEventCallback[
            _InstallationRepositoriesPayload
        ]
        | EventEndpoint,
    },
    total=False,
)
"""Event handlers that start runs. Return a prompt (string, message, or state),
or ``None`` to skip. While a thread waits on an interrupt, the handler still runs,
with ``thread.interrupted`` set: a run it returns for that thread doesn't start,
and has the issue or pull request told to continue it in LangSmith Studio, while
runs on other threads start.

Example::

    {"issues.opened": lambda e, _: f"Help with #{e.payload['issue']['number']}"}
"""


GitHubIssuePayload = _IssuePayload
GitHubIssueCommentPayload = _IssueCommentPayload
GitHubLabeledIssuePayload = _LabeledIssuePayload
GitHubPullRequestPayload = _PullRequestPayload
GitHubLabeledPullRequestPayload = _LabeledPullRequestPayload
GitHubReviewPayload = _ReviewPayload
GitHubReviewCommentPayload = _ReviewCommentPayload
GitHubWorkflowPayload = _WorkflowPayload
GitHubCheckRunPayload = _CheckRunPayload
GitHubCheckSuitePayload = _CheckSuitePayload
GitHubMergeGroupPayload = _MergeGroupPayload
GitHubMergeGroupDestroyedPayload = _MergeGroupDestroyedPayload
GitHubPushPayload = _PushPayload
GitHubStatusPayload = _StatusPayload
GitHubInstallationEventPayload = _InstallationPayload
GitHubInstallationRepositoriesPayload = _InstallationRepositoriesPayload
