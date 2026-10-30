"""Each workspace's curator: one long-lived system thread, woken daily while memories wait.

The thread is public so anyone can follow what it decides; it never sees what a
private thread said unless that thread's person shared it (``MEMORIES.for_curator``).
"""

import logging
from datetime import timedelta
from typing import Final

from openswe import event_claims
from openswe.dispatch import create_durable_run, dispatch_client
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.jobs.queue import Job
from openswe.memories.store import MEMORIES
from openswe.prompts import prompt
from openswe.thread_ids import curator_thread_id
from openswe.threads import succession
from openswe.threads.creation import create_thread

logger = logging.getLogger(__name__)

WAKE_JOB: Final = "workspace_curator_wake"
WAKE_SCHEDULE: Final = "17 6 * * *"
# The thread metadata key naming the workspace a curator thread curates.
CURATOR_KEY: Final = "curator"
_SENDER_ID: Final = "system:curator"
_ASSISTANT_ID: Final = "agent"
_FIRE_CLAIM_TTL: Final = timedelta(days=2)


async def wake_curator(slug: str) -> str:
    """Start a run on ``slug``'s curator thread, creating the thread the first time."""
    client = dispatch_client()
    thread_id = await succession.succeed(curator_thread_id(slug))
    await create_thread(
        client,
        thread_id,
        title=f"Memory curator: {slug}",
        if_exists="do_nothing",
        metadata={
            "owner_type": "system",
            "visibility": "public",
            "workspace": slug,
            CURATOR_KEY: slug,
        },
    )
    configurable = {"thread_id": thread_id, "workspace": slug}
    run = await create_durable_run(
        thread_id,
        _ASSISTANT_ID,
        input=build_run_input(
            prompt("curator/wake", workspace=slug),
            {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
            systems=[{"id": _SENDER_ID, "display_name": "Memory curator", "platform": "open-swe"}],
        ),
        source="curator",
        thread_title=None,
        config={"configurable": with_invocation_id(configurable, new_invocation_id())},
        client=client,
        multitask_strategy="enqueue",
    )
    return str(run["run_id"])


async def wake_curators(job: Job) -> None:
    """Wake the curator of every workspace with a memory it has not decided."""
    # Once per fire: a job claimed again after a deploy or crash may have woken them already.
    if not await event_claims.claim(WAKE_JOB, job.due_at.isoformat(), ttl=_FIRE_CLAIM_TTL):
        return
    for slug in await MEMORIES.pending_workspaces():
        try:
            run_id = await wake_curator(slug)
        except Exception:
            logger.exception("Could not wake a workspace's curator", extra={"workspace": slug})
            continue
        logger.info("Woke a workspace's curator", extra={"workspace": slug, "run_id": run_id})
