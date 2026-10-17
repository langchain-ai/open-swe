"""GitHub issue events this deployment's channel receives, handed to the Open SWE backend.

MDA's GitHub channel verifies each delivery and owns the conversation: one thread
per issue, on which it starts the run. The backend decides the rest, as for any
Open SWE run: whether the event starts one, its input, and the run context with
the token the run calls back with, signed for the channel's thread.
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, JsonValue

from rt import intake

if TYPE_CHECKING:
    # Types only: the backend's tests import this module without the SDK.
    from managed_deepagents import (
        ChannelRunOptions,
        ChannelRunResult,
        GitHubChannelThread,
        GitHubEvent,
        GitHubPayload,
    )

_RUNS_PATH: Final = "/github/channel/runs"
# Starts nothing: the channel only asks whether to tell the issue the thread waits.
_ACTS_WHILE_INTERRUPTED: Final = "Open SWE was mentioned while the thread waits."


class _PreparedRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]


class _Prepared(BaseModel):
    run: _PreparedRun | None = None
    acts: bool = False


class GitHubIntake:
    """Hands this deployment's GitHub issue events to the Open SWE backend."""

    # Generic over the payload, so one handler serves each event key's own type.
    async def start_run[P: "GitHubPayload"](
        self, event: "GitHubEvent[P]", thread: "GitHubChannelThread"
    ) -> "ChannelRunResult | None":
        """The run an @openswe mention on an issue starts, as the backend prepared it."""
        prepared = _Prepared.model_validate(
            await intake.post(
                _RUNS_PATH,
                {
                    "thread_id": thread.id,
                    "interrupted": thread.interrupted,
                    "delivery_id": event.delivery_id,
                    "event": event.key,
                    "payload": dict(event.payload),
                },
            )
        )
        if thread.interrupted:
            return _ACTS_WHILE_INTERRUPTED if prepared.acts else None
        if prepared.run is None:
            return None
        options: "ChannelRunOptions" = {
            "context": prepared.run.context,
            "metadata": prepared.run.metadata,
        }
        return (prepared.run.input, options)


GITHUB: Final = GitHubIntake()
