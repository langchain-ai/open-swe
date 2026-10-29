"""The calls MDA's Linear channel makes on the backend, with no run yet.

The channel verifies each Linear delivery and owns its conversations. It hands
every delivery it handles to the backend's event log and automations
(``record_linear_delivery``), and for an agent session turn asks the backend for
what only it knows (``start_linear_session``): who the author is in Open SWE,
the repository and workspace, and the signed run context. The channel builds
the run's input and says why nothing started itself.
"""

import json
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator

from runtime_api.endpoint import CHANNEL_API_PREFIX, Endpoint

# Linear's deliveries are a few kilobytes; anything near this is not one.
MAX_PAYLOAD_CHARS: Final = 1_000_000

LinearEventKey = Literal[
    "Issue.create",
    "Issue.update",
    "Comment.create",
    "AgentSessionEvent.created",
    "AgentSessionEvent.prompted",
]


class LinearDelivery(BaseModel):
    """A delivery the channel verified, as Linear sent it."""

    key: LinearEventKey
    delivery_id: str = Field(min_length=1)
    payload: dict[str, JsonValue]

    @field_validator("payload")
    @classmethod
    def _bounded(cls, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(json.dumps(payload)) > MAX_PAYLOAD_CHARS:
            raise ValueError("payload is too large for a Linear delivery")
        return payload


class LinearDeliveryRecorded(BaseModel):
    """The delivery is in the event log, and its automations are on their way."""


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


class LinearSessionRun(BaseModel):
    """Start the turn's run on the issue's repository, with this context."""

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


RECORD_LINEAR_DELIVERY: Final = Endpoint(
    "record_linear_delivery",
    LinearDelivery,
    LinearDeliveryRecorded,
    seconds=60.0,
    # Recorded once per delivery ID, so a repeat logs and launches nothing again.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
START_LINEAR_SESSION: Final = Endpoint(
    "start_linear_session",
    LinearSessionTurn,
    LinearSessionStart,
    seconds=60.0,
    prefix=CHANNEL_API_PREFIX,
)
