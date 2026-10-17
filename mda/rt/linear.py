"""Linear events this deployment's channel receives, handed to the Open SWE backend.

MDA's Linear channel verifies each delivery (Linear's signature and timestamp) and
owns the conversation: it starts the run on its thread, one per agent session, so
the answer, Stop and errors reach the Linear session. The backend decides the rest,
as for any Open SWE run, from the event's validated payload. Issue and comment
events start no conversation: the channel forwards them to the backend.
"""

from typing import TYPE_CHECKING, Final

from managed_deepagents import (
    ChannelRunOptions,
    ChannelRunResult,
    LinearChannelThread,
    LinearEvent,
    LinearSessionEvent,
)
from pydantic import BaseModel, JsonValue

from rt import intake

if TYPE_CHECKING:
    import httpx

#: Session turns come to the backend as the runs they start; the rest as Linear sent them.
_SESSION_KEYS: Final = frozenset({"AgentSessionEvent.created", "AgentSessionEvent.prompted"})


class _PreparedRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]


class _Prepared(BaseModel):
    run: _PreparedRun | None


class LinearIntake:
    """Hands this deployment's Linear events to the Open SWE backend."""

    async def start_run(
        self, event: LinearSessionEvent, thread: LinearChannelThread
    ) -> ChannelRunResult | None:
        """The run an agent session turn starts, as the backend prepared it; None declines.

        A failure raises, so the channel tells the session something went wrong.
        """
        prepared = _Prepared.model_validate(
            await intake.post(
                "/linear/channel/runs",
                {
                    "thread_id": thread.id,
                    "delivery_id": event.delivery_id,
                    "key": event.key,
                    "payload": dict(event.payload),
                },
            )
        ).run
        if prepared is None:
            return None
        options: ChannelRunOptions = {"context": prepared.context, "metadata": prepared.metadata}
        return (prepared.input, options)

    def forward(self, request: "httpx.Request", event: LinearEvent) -> "httpx.Request | None":
        """Send an event that starts no conversation on to the backend, as Linear sent it.

        The backend logs it and runs the automations it triggers.
        """
        if event.key in _SESSION_KEYS:
            return None
        return intake.forward(request, "/linear/channel/events")


LINEAR: Final = LinearIntake()
