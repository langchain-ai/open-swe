"""Complete an MDA channel's run once the channel reports that it ended.

The channel starts the run itself, so no run-complete webhook follows it. The
channel watches each run it starts and reports how it ended (its ``on_settled``
hook), which is where the backend completes it, from the run MDA recorded, as the
webhook does for its own runs. The report comes from the process that started the
run, so one lost to a restart is the thread status reconcile job's
(``openswe.threads.status``).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from openswe.dispatch import channel_of
from openswe.mda_channels.auth import require_mda_channel
from openswe.threads import index
from openswe.threads.status import complete_ended_run

router = APIRouter(prefix="/mda/channel", dependencies=[Depends(require_mda_channel)])


class SettledRun(BaseModel):
    """A run a channel started on its thread, which ended."""

    thread_id: UUID
    run_id: str = Field(min_length=1, max_length=200)


@router.post("/settled")
async def complete_settled_run(run: SettledRun) -> dict[str, bool]:
    """Complete a channel's run that ended; any other thread's runs have their webhook."""
    thread = await index.get(str(run.thread_id))
    if thread is None or thread.runtime != "mda" or channel_of(thread.metadata) is None:
        return {"completed": False}
    return {"completed": await complete_ended_run(thread, run.run_id)}
