"""The calls MDA's Linear channel makes on the backend, with no run yet.

The channel's handlers decide what each Linear delivery starts; the backend
answers only what needs its data or its signing key:

- Every handled delivery goes to the event log, which wakes the threads that
  listen for Linear events: the first call for a delivery logs it, so each event
  takes at most two calls. ``record_linear_delivery`` logs one that asks nothing else.
- ``linear_session_facts``, then ``plan_linear_session``: what Open SWE knows about
  a session turn's author, thread and repositories, from which the channel picks
  the repository and workspace; then the backend checks that choice again, indexes
  the thread and signs the run's context.
- ``linear_automation_rules``, then ``plan_linear_automations``: a team's Linear
  automations, which the channel matches against an issue event and writes each
  match's prompt for; then, for every match at once, the backend claims each once
  per delivery, writes its run's input and signs its context, on a thread of its own.
"""

import json
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator

from runtime_api.endpoint import CHANNEL_API_PREFIX, Endpoint

# Linear's deliveries are a few kilobytes; anything near this is not one.
MAX_PAYLOAD_CHARS: Final = 1_000_000
# Well above a written prompt: an automation's own is at most 20,000 characters,
# and the issue description it quotes 8,000.
MAX_AUTOMATION_PROMPT_CHARS: Final = 100_000
# A Linear team key, such as ``ENG``.
TEAM_KEY_PATTERN: Final = r"^[A-Z][A-Z0-9_]{0,9}$"
# ``owner/name``, as GitHub spells a repository.
REPOSITORY_PATTERN: Final = r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$"

LinearEventKey = Literal[
    "Issue.create",
    "Issue.update",
    "Comment.create",
    "AgentSessionEvent.created",
    "AgentSessionEvent.prompted",
]


def _bounded(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if len(json.dumps(payload)) > MAX_PAYLOAD_CHARS:
        raise ValueError("payload is too large for a Linear delivery")
    return payload


class LinearDelivery(BaseModel):
    """A delivery the channel verified, as Linear sent it."""

    key: LinearEventKey
    delivery_id: str = Field(min_length=1)
    payload: dict[str, JsonValue]

    _bounded_payload = field_validator("payload")(_bounded)


class LinearDeliveryRecorded(BaseModel):
    """The delivery is in the event log."""


class LinearIssue(BaseModel):
    id: str = Field(min_length=1)
    identifier: str = ""
    title: str = ""
    description: str | None = None
    url: str = ""


class LinearPerson(BaseModel):
    """Who wrote a session turn; Linear names an Open SWE user by ``email``."""

    id: str = ""
    name: str = ""
    email: str = ""


class LinearSessionTurn(BaseModel):
    """One turn of a Linear agent session: who asked what about which issue."""

    # The session's thread, which the channel starts the run on.
    thread_id: UUID
    session_id: str = Field(min_length=1)
    issue: LinearIssue
    author: LinearPerson
    body: str
    comment_id: str = ""


class LinearSessionFactsRequest(BaseModel):
    # The turn's delivery, which the backend logs once before it answers.
    delivery: LinearDelivery
    thread_id: UUID
    author_email: str = ""


# Whether the author can run as themselves on GitHub: they signed in to Open SWE with
# the email Linear knows them by (``signed_in``), never did (``sign_in``), or their
# GitHub sign-in is no longer valid (``sign_in_again``).
AuthorStanding = Literal["signed_in", "sign_in", "sign_in_again"]


class RepositoryChoice(BaseModel):
    repository: str = Field(pattern=REPOSITORY_PATTERN)
    # The workspace whose default chose it; ``None`` for a default that applies to all.
    workspace: str | None = None


class RepositoryPolicy(BaseModel):
    """Which repositories Open SWE may work in, and how a bare name is read."""

    # The owner of a repository a turn names without one (``repo name``).
    default_owner: str | None = None
    # Lowercased; both empty allows every repository.
    allowed_orgs: list[str] = []
    allowed_repos: list[str] = []


class LinearSessionFacts(BaseModel):
    """What Open SWE knows about a session turn, for the channel to pick its repository."""

    author: AuthorStanding
    # Where the author signs in, when the backend has a dashboard URL.
    settings_url: str | None = None
    # What the session's thread already works in, from its earlier turns.
    thread_repository: str | None = None
    thread_workspace: str | None = None
    # The author's default for the thread's workspace, else their global one.
    personal_default: RepositoryChoice | None = None
    # The instance's default repository.
    instance_default: str | None = None
    policy: RepositoryPolicy


class PlanLinearSession(BaseModel):
    """Start the turn's run in ``repository``: the channel's choice, which the backend checks."""

    turn: LinearSessionTurn
    repository: str = Field(pattern=REPOSITORY_PATTERN)
    # The workspace the turn runs in; ``None`` for the one that owns the repository.
    workspace: str | None = None


class LinearSessionRun(BaseModel):
    """Start the turn's run on the session's thread, with this context."""

    kind: Literal["run"] = "run"
    # ``owner/name``.
    repository: str
    title: str
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


DeclineReason = Literal[
    # The turn, its thread and its issue name no repository, and no default applies.
    "no_repository",
    # The repository named is outside the allowlist.
    "repository_not_allowed",
    # No Open SWE user signed in to GitHub has the author's email.
    "sign_in",
    # The author's GitHub sign-in is no longer valid.
    "sign_in_again",
]


class LinearSessionDeclined(BaseModel):
    """Start nothing; the channel tells the session why."""

    kind: Literal["declined"] = "declined"
    reason: DeclineReason
    # The repository refused, for ``repository_not_allowed``.
    repository: str | None = None
    # Where the author signs in, when the backend has a dashboard URL.
    settings_url: str | None = None


class LinearSessionStart(BaseModel):
    answer: Annotated[LinearSessionRun | LinearSessionDeclined, Field(discriminator="kind")]


LinearTriggerEvent = Literal["issue.created", "issue.labeled"]


class LinearTrigger(BaseModel):
    """When a Linear automation fires, for one team."""

    events: list[LinearTriggerEvent]
    # Label names: the issue must carry one, or for ``issue.labeled``, gain one.
    labels: list[str] = []
    project: str | None = None


class LinearAutomation(BaseModel):
    id: str = Field(min_length=1)
    # What the automation asks its run to do, which the channel's prompt leads with.
    prompt: str
    triggers: list[LinearTrigger]


class LinearAutomationRulesRequest(BaseModel):
    # The issue event's delivery, which the backend logs once before it answers.
    delivery: LinearDelivery
    team: str = Field(pattern=TEAM_KEY_PATTERN)


class LinearAutomationRules(BaseModel):
    """The team's enabled Linear automations."""

    automations: list[LinearAutomation]
    # Whether the issue's creator is an Open SWE user: otherwise the channel fences
    # the issue it quotes as untrusted.
    creator_registered: bool = False


class FiredLinearAutomation(BaseModel):
    """An automation the channel matched an issue event to, the event it fires on, and
    the prompt the channel wrote for its run."""

    automation_id: str = Field(min_length=1)
    event: LinearTriggerEvent
    prompt: str = Field(min_length=1, max_length=MAX_AUTOMATION_PROMPT_CHARS)


class PlanLinearAutomation(BaseModel):
    """Start ``automation`` for the issue event the channel matched it to."""

    automation_id: str = Field(min_length=1)
    delivery_id: str = Field(min_length=1)
    event: LinearTriggerEvent
    team: str = Field(pattern=TEAM_KEY_PATTERN)
    prompt: str = Field(min_length=1, max_length=MAX_AUTOMATION_PROMPT_CHARS)


class PlanLinearAutomations(BaseModel):
    """Start every automation the channel matched one issue event to."""

    delivery_id: str = Field(min_length=1)
    automations: list[FiredLinearAutomation] = Field(min_length=1, max_length=100)
    team: str = Field(pattern=TEAM_KEY_PATTERN)

    def each(self) -> list[PlanLinearAutomation]:
        """One plan per automation, in order."""
        return [
            PlanLinearAutomation(
                automation_id=fired.automation_id,
                delivery_id=self.delivery_id,
                event=fired.event,
                team=self.team,
                prompt=fired.prompt,
            )
            for fired in self.automations
        ]


class LinearAutomationRun(BaseModel):
    """Start the automation's run on ``thread_id``, a thread of its own for this delivery."""

    thread_id: UUID
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class PlannedLinearAutomations(BaseModel):
    # One per automation, in the order asked: ``None`` when it starts nothing, because
    # the delivery already ran it, it hit its hourly limit, or it can't run any more.
    runs: list[LinearAutomationRun | None]


RECORD_LINEAR_DELIVERY: Final = Endpoint(
    "record_linear_delivery",
    LinearDelivery,
    LinearDeliveryRecorded,
    seconds=60.0,
    # Recorded once per delivery ID, so a repeat logs nothing again.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
LINEAR_SESSION_FACTS: Final = Endpoint(
    "linear_session_facts",
    LinearSessionFactsRequest,
    LinearSessionFacts,
    seconds=60.0,
    # Logs the delivery once per delivery ID, and only reads otherwise.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
PLAN_LINEAR_SESSION: Final = Endpoint(
    "plan_linear_session",
    PlanLinearSession,
    LinearSessionStart,
    seconds=60.0,
    prefix=CHANNEL_API_PREFIX,
)
LINEAR_AUTOMATION_RULES: Final = Endpoint(
    "linear_automation_rules",
    LinearAutomationRulesRequest,
    LinearAutomationRules,
    seconds=60.0,
    # Logs the delivery once per delivery ID, and only reads otherwise.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
PLAN_LINEAR_AUTOMATIONS: Final = Endpoint(
    "plan_linear_automations",
    PlanLinearAutomations,
    PlannedLinearAutomations,
    # Each automation's planning signs a run; a team rarely fires more than a few at once.
    seconds=120.0,
    # Claimed once per automation and delivery, so a repeat plans the same threads,
    # whose runs start once.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
