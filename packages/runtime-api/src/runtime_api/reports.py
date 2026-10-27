"""What the runtime tells the backend happened, sent in batches the backend acknowledges.

The runtime gathers its reports (each reply and tool call, each inbox receipt)
and its notices (work the backend does for the run whose answer the run never
reads) and sends them in the order it made them, as many as are waiting in one
``reports`` call. Each item carries a key unique in its thread, and the backend
records a key once, so a batch sent again after its answer was lost records
nothing twice. An item stays the runtime's until an answer names it taken.

The backend takes items in order and stops at the first it cannot take now: one
whose recording failed, or one another delivery of the same key is still
recording. It answers with a reply for each item it took. What it did not take is sent again, so the transcript never sees a
report before the ones made before it.
"""

from collections.abc import Mapping
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from runtime_api.endpoint import LOOP_CALL_SECONDS, Delivery, Endpoint
from runtime_api.loop import (
    FINISH_SCOUT_PLAN,
    INBOX_RECEIVED,
    MODEL_RESULT,
    SANDBOX_UNREACHABLE,
    SETTLE_REVIEW_CHECK,
    STEP_LIMIT_REACHED,
    TOOL_RESULT,
    TOOL_STARTED,
    InboxReceipt,
    ModelResult,
    SandboxGone,
    ScoutPlanEnd,
    ToolCallReport,
    ToolResultReport,
)
from runtime_api.operations import RunRequest


class Sent(BaseModel):
    """One report or notice, as the runtime made it."""

    # Unique in the thread: the backend records each key once.
    key: str
    # The trace position of the call that made it, which the backend nests its spans under.
    trace: dict[str, str] = {}


class ToolStarted(Sent):
    endpoint: Literal["tool_started"]
    request: ToolCallReport


class ToolFinished(Sent):
    endpoint: Literal["tool_result"]
    request: ToolResultReport


class Replied(Sent):
    endpoint: Literal["model_result"]
    request: ModelResult


class Received(Sent):
    endpoint: Literal["inbox_received"]
    request: InboxReceipt


class Noticed(Sent):
    endpoint: Literal["step_limit_reached", "settle_review_check"]
    request: RunRequest


class SandboxNoticed(Sent):
    endpoint: Literal["sandbox_unreachable"]
    request: SandboxGone


class ScoutPlanNoticed(Sent):
    endpoint: Literal["finish_scout_plan"]
    request: ScoutPlanEnd


type Item = Annotated[
    ToolStarted | ToolFinished | Replied | Received | Noticed | SandboxNoticed | ScoutPlanNoticed,
    Field(discriminator="endpoint"),
]
_item: TypeAdapter[Item] = TypeAdapter(Item)

# How each call an item stands for may be delivered, by name. One the backend may
# repeat harmlessly (``idempotent``) is recorded without claiming its key.
REPORTED: Final[Mapping[str, Delivery]] = {
    endpoint.name: endpoint.delivery
    for endpoint in (
        TOOL_STARTED,
        TOOL_RESULT,
        MODEL_RESULT,
        INBOX_RECEIVED,
        SANDBOX_UNREACHABLE,
        STEP_LIMIT_REACHED,
        SETTLE_REVIEW_CHECK,
        FINISH_SCOUT_PLAN,
    )
}


def item[Request: BaseModel, Response: BaseModel](
    endpoint: Endpoint[Request, Response],
    request: Request,
    *,
    key: str,
    trace: Mapping[str, str],
) -> Item:
    """``request`` to ``endpoint``, as an item of a batch; ``endpoint`` must be ``REPORTED``."""
    return _item.validate_python(
        {"endpoint": endpoint.name, "request": request, "key": key, "trace": dict(trace)}
    )


class Reports(BaseModel):
    # Oldest first: the backend takes them in this order.
    items: list[Item]


class ReportsTaken(BaseModel):
    """What the backend took: the first items sent, one reply each, in order. The rest
    are the runtime's to send again."""

    # What each taken item's endpoint answered, such as a tool result's git evidence.
    replies: list[dict[str, JsonValue]] = []
    # Why the backend could not record the first item it did not take, when it tried
    # it and failed; nothing after it was tried.
    refused: str | None = None


# Every item is recorded once by its key, so a batch is safe to send again.
REPORTS = Endpoint(
    "reports", Reports, ReportsTaken, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
