"""The calls MDA's GitHub channel makes on the backend, with no run yet.

The channel receives Open SWE's GitHub App deliveries in the MDA deployment and
decides there what each one does: which of Open SWE's work it starts, from its
event, its action and what Open SWE knows about its repository. The backend
answers only what needs its data, its GitHub App or its signing key:

- ``record_github_delivery``: logs the delivery, and says whether a workspace owns
  its repository, whether the allowlist takes it, whether automatic review is on
  for it, and the handles that mention Open SWE.
- ``act_on_github_delivery``: runs the work the channel chose for the delivery (a
  review, a CI wakeup, a pull request comment's run, ...), once per delivery, after
  it answers; plans the runs of the automations the delivery fires, each on a thread
  of its own; and for an ``@openswe`` mention on an issue, plans the run it starts
  on the issue's conversation. The channel starts the planned runs.

So a delivery takes at most two calls.
"""

import json
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from runtime_api.endpoint import CHANNEL_API_PREFIX, Endpoint

# GitHub's deliveries are at most 25 MB; anything near this is no event Open SWE acts on.
MAX_PAYLOAD_CHARS: Final = 5_000_000


class GitHubDelivery(BaseModel):
    """A delivery the channel verified, as GitHub sent it."""

    # GitHub's event name (``X-GitHub-Event``), such as ``pull_request``.
    event: str = Field(min_length=1, max_length=64)
    delivery_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, JsonValue]

    @field_validator("payload")
    @classmethod
    def _bounded(cls, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(json.dumps(payload)) > MAX_PAYLOAD_CHARS:
            raise ValueError("payload is too large for a GitHub delivery")
        return payload


class GitHubDeliveryFacts(BaseModel):
    """What Open SWE knows about a delivery's repository, for the channel to route it."""

    # A workspace owns the repository, or the delivery names none.
    routable: bool
    # The repository allowlist takes it.
    allowed: bool
    # Automatic review is on for the repository; only read for pull requests and pushes.
    auto_review: bool
    # The handles that mention Open SWE, lowercased, such as ``@openswe``.
    tags: list[str]


# The backend's work a delivery can start, each as its webhook ran it.
type GitHubWorkName = Literal[
    # The native merge queue reads the queue again.
    "wake_merge_queue",
    # The App was installed, removed or suspended, or its repositories changed:
    # everyone's cached list of the repositories they reach through it is dropped.
    "forget_cached_repos",
    # The agent thread that opened a pull request learns its state, and its usage.
    "track_pull_request_state",
    "track_pull_request_usage",
    # A push to a pull request's branch: superseded approvals withdrawn, its guides
    # paused, and a fork's head reviewed again.
    "withdraw_superseded_approvals",
    "notify_pr_updated",
    "review_fork_push",
    "settle_review_on_close",
    "settle_human_reviews",
    "update_reviewer_watch",
    # Automatic review of a pull request opened or marked ready, behind the public-repo gate.
    "review_pull_request",
    "review_push",
    "ci_event",
    "record_completed_review",
    # A pull request comment, review or review comment: who wrote it, whether it
    # answers a review finding or wakes the agent thread that opened the pull
    # request, the public-repo gate, then its run.
    "route_comment",
]


class GitHubIssueRunRequest(BaseModel):
    """An ``@openswe`` mention on an issue: plan the run it starts on the issue's thread."""

    # The issue's conversation thread, which the channel starts the run on.
    thread_id: UUID
    # The thread waits on an interrupt: the backend only says whether Open SWE would act.
    waiting: bool = False


class ActOnGitHubDelivery(GitHubDelivery):
    """What the channel decided a delivery starts."""

    work: list[GitHubWorkName] = []
    # Whether the comment ``route_comment`` routes mentions Open SWE.
    mentioned: bool = False
    # The delivery may fire automations: an issue opened, a pull request opened or closed.
    automations: bool = False
    issue_run: GitHubIssueRunRequest | None = None

    @model_validator(mode="after")
    def _acts(self) -> Self:
        if not self.work and self.issue_run is None and not self.automations:
            raise ValueError("a delivery that starts nothing needs no call")
        return self


class GitHubIssueRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class PlannedGitHubIssueRun(BaseModel):
    # Whether Open SWE acts: false when the commenter isn't an Open SWE user, the
    # public-repo gate refuses the sender, or the issue gives the agent nothing to do.
    starts: bool
    # The run, unless the thread waits.
    run: GitHubIssueRun | None = None


class GitHubAutomationRun(BaseModel):
    """Start the automation's run on ``thread_id``, a thread of its own for this delivery."""

    thread_id: UUID
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class GitHubDeliveryActed(BaseModel):
    """The backend took the work, which it runs after answering, as it did for its webhook."""

    # The issue's run, when the call asked for one.
    issue_run: PlannedGitHubIssueRun | None = None
    # A run per automation the delivery fires, each claimed once per delivery. A call
    # sent again plans the runs it already claimed on the same threads, where the
    # channel starts each delivery's run once.
    automations: list[GitHubAutomationRun] = []


RECORD_GITHUB_DELIVERY: Final = Endpoint(
    "record_github_delivery",
    GitHubDelivery,
    GitHubDeliveryFacts,
    seconds=60.0,
    # Recorded once per delivery ID, so a repeat logs nothing again.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
ACT_ON_GITHUB_DELIVERY: Final = Endpoint(
    "act_on_github_delivery",
    ActOnGitHubDelivery,
    GitHubDeliveryActed,
    # Each automation's planning signs a run; a repository rarely fires more than a few at once.
    seconds=120.0,
    # The work is claimed once per delivery ID, but planning an issue's run reacts to
    # the comment and indexes the thread, so a call isn't sent again blindly.
    prefix=CHANNEL_API_PREFIX,
)
