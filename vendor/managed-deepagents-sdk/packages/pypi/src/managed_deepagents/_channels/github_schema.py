"""Separate receipt and dispatch schemas for handwritten GitHub payload subsets."""

from typing import Annotated, Literal, NotRequired

from pydantic import ConfigDict, Field, TypeAdapter, with_config
from typing_extensions import TypedDict

from managed_deepagents._channels.scalars import Nonblank

_SafeInteger = Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]


@with_config(ConfigDict(extra="allow", strict=True))
class _Repository(TypedDict):
    id: NotRequired[_SafeInteger]


@with_config(ConfigDict(extra="allow", strict=True))
class _Subject(TypedDict):
    number: NotRequired[_SafeInteger]


@with_config(ConfigDict(extra="allow", strict=True))
class _Payload(TypedDict):
    action: NotRequired[str]
    repository: NotRequired[_Repository]
    issue: NotRequired[_Subject]
    pull_request: NotRequired[_Subject]


class GitHubReceipt(TypedDict):
    event: Nonblank
    delivery_id: Nonblank
    payload: _Payload


github_receipt_adapter = TypeAdapter(GitHubReceipt)

# Dispatch schemas are stronger than receipt schemas, and are used only when a
# matching callback is registered. Preserve unknown provider fields at every level.
_PositiveId = Annotated[_SafeInteger, Field(gt=0)]


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubIssue(TypedDict):
    number: _PositiveId
    title: Nonblank
    body: str | None
    pull_request: NotRequired[dict[str, object]]


class GitHubPullRequest(GitHubIssue):
    draft: NotRequired[bool]
    merged: NotRequired[bool]


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubComment(TypedDict):
    id: _PositiveId
    body: str


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubLabel(TypedDict):
    name: Nonblank


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubReview(TypedDict):
    id: _PositiveId
    body: str | None
    state: Nonblank


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubWorkflowRun(TypedDict):
    id: _PositiveId
    conclusion: str | None
    pull_requests: list[dict[str, object]]


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubCheckRun(TypedDict):
    id: _PositiveId
    name: Nonblank
    head_sha: Nonblank
    conclusion: str | None
    pull_requests: list[dict[str, object]]


@with_config(ConfigDict(extra="allow", strict=True))
class GitHubCheckSuite(TypedDict):
    id: _PositiveId
    head_sha: Nonblank
    conclusion: str | None
    pull_requests: list[dict[str, object]]


@with_config(ConfigDict(extra="allow", strict=True))
class _Owner(TypedDict):
    login: Nonblank


@with_config(ConfigDict(extra="allow", strict=True))
class _DispatchRepository(TypedDict):
    id: _PositiveId
    owner: _Owner
    name: Nonblank
    full_name: Nonblank
    private: bool


@with_config(ConfigDict(extra="allow", strict=True))
class _Sender(TypedDict):
    id: _PositiveId
    login: Nonblank
    type: Literal["User", "Bot", "Organization"]


@with_config(ConfigDict(extra="allow", strict=True))
class _Installation(TypedDict):
    id: _PositiveId


@with_config(ConfigDict(extra="allow", strict=True))
class _DispatchBase(TypedDict):
    repository: _DispatchRepository
    sender: _Sender
    installation: _Installation


class _DispatchPayload(_DispatchBase):
    action: Nonblank


class _IssuePayload(_DispatchPayload):
    issue: GitHubIssue


class _LabeledIssuePayload(_IssuePayload):
    label: GitHubLabel


class _IssueCommentPayload(_IssuePayload):
    comment: GitHubComment


class _PullRequestPayload(_DispatchPayload):
    pull_request: GitHubPullRequest


class _LabeledPullRequestPayload(_PullRequestPayload):
    label: GitHubLabel


class _ReviewPayload(_PullRequestPayload):
    review: GitHubReview


class _ReviewCommentPayload(_PullRequestPayload):
    comment: GitHubComment


class _WorkflowPayload(_DispatchPayload):
    workflow_run: GitHubWorkflowRun


class _CheckRunPayload(_DispatchPayload):
    check_run: GitHubCheckRun


class _CheckSuitePayload(_DispatchPayload):
    check_suite: GitHubCheckSuite


# Events without an action are keyed by their name alone.
class _PushPayload(_DispatchBase):
    ref: Nonblank
    before: Nonblank
    after: Nonblank


class _StatusPayload(_DispatchBase):
    id: _PositiveId
    sha: Nonblank
    state: Nonblank
    context: Nonblank


GitHubPayload = (
    _IssuePayload
    | _LabeledIssuePayload
    | _IssueCommentPayload
    | _PullRequestPayload
    | _LabeledPullRequestPayload
    | _ReviewPayload
    | _ReviewCommentPayload
    | _WorkflowPayload
    | _CheckRunPayload
    | _CheckSuitePayload
    | _PushPayload
    | _StatusPayload
)
SupportedEventKey = Literal[
    "issues.opened",
    "issues.edited",
    "issues.reopened",
    "issues.labeled",
    "issue_comment.created",
    "issue_comment.edited",
    "pull_request.opened",
    "pull_request.edited",
    "pull_request.reopened",
    "pull_request.ready_for_review",
    "pull_request.converted_to_draft",
    "pull_request.synchronize",
    "pull_request.labeled",
    "pull_request.closed",
    "pull_request_review.submitted",
    "pull_request_review.edited",
    "pull_request_review.dismissed",
    "pull_request_review_comment.created",
    "pull_request_review_comment.edited",
    "push",
    "status",
    "check_run.completed",
    "check_suite.completed",
    "workflow_run.completed",
]

# Several event actions share a payload shape; routing chooses the validator.
_issue_adapter: TypeAdapter[GitHubPayload] = TypeAdapter(_IssuePayload)
_comment_adapter: TypeAdapter[GitHubPayload] = TypeAdapter(_IssueCommentPayload)
_pr_adapter: TypeAdapter[GitHubPayload] = TypeAdapter(_PullRequestPayload)
_review_adapter: TypeAdapter[GitHubPayload] = TypeAdapter(_ReviewPayload)
_review_comment_adapter: TypeAdapter[GitHubPayload] = TypeAdapter(_ReviewCommentPayload)
_PAYLOAD_ADAPTERS: dict[SupportedEventKey, TypeAdapter[GitHubPayload]] = {
    "issues.opened": _issue_adapter,
    "issues.edited": _issue_adapter,
    "issues.reopened": _issue_adapter,
    "issues.labeled": TypeAdapter(_LabeledIssuePayload),
    "issue_comment.created": _comment_adapter,
    "issue_comment.edited": _comment_adapter,
    "pull_request.opened": _pr_adapter,
    "pull_request.edited": _pr_adapter,
    "pull_request.reopened": _pr_adapter,
    "pull_request.ready_for_review": _pr_adapter,
    "pull_request.converted_to_draft": _pr_adapter,
    "pull_request.synchronize": _pr_adapter,
    "pull_request.labeled": TypeAdapter(_LabeledPullRequestPayload),
    "pull_request.closed": _pr_adapter,
    "pull_request_review.submitted": _review_adapter,
    "pull_request_review.edited": _review_adapter,
    "pull_request_review.dismissed": _review_adapter,
    "pull_request_review_comment.created": _review_comment_adapter,
    "pull_request_review_comment.edited": _review_comment_adapter,
    "push": TypeAdapter(_PushPayload),
    "status": TypeAdapter(_StatusPayload),
    "check_run.completed": TypeAdapter(_CheckRunPayload),
    "check_suite.completed": TypeAdapter(_CheckSuitePayload),
    "workflow_run.completed": TypeAdapter(_WorkflowPayload),
}
github_identifier_adapter = TypeAdapter(_PositiveId)
SUPPORTED_EVENT_KEYS = frozenset(_PAYLOAD_ADAPTERS)


def validate_payload(key: SupportedEventKey, payload: object) -> GitHubPayload:
    validated = _PAYLOAD_ADAPTERS[key].validate_python(payload, strict=True)
    _, _, action = key.partition(".")
    # Events without an action, such as `push`, must not carry one.
    if validated.get("action") != (action or None):
        raise ValueError("GitHub action does not match event key")
    return validated
