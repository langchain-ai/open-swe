"""Each workspace's curator: one long-lived system thread, woken daily while engrams wait.

The thread is public so anyone can follow what it decides; it never sees what a
private thread said unless that thread's person shared it (``ENGRAMS.for_curator``).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Self

from openswe import event_claims
from openswe.dispatch import create_durable_run, dispatch_client
from openswe.engrams.store import CURATOR_SOURCE, CURATOR_THREAD_KEY, ENGRAMS
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.threads import succession
from openswe.threads.creation import create_thread

logger = logging.getLogger(__name__)

WAKE_JOB: Final = "workspace_curator_wake"
WAKE_SCHEDULE: Final = "17 6 * * *"
_SENDER_ID: Final = "system:curator"
_ASSISTANT_ID: Final = "agent"
_FIRE_CLAIM_TTL: Final = timedelta(days=2)


@dataclass(frozen=True)
class WorkspaceCurator:
    """The curator of the workspace stored under ``slug``."""

    slug: str

    @classmethod
    def for_run(cls) -> Self:
        """The curator whose thread this run is in; only its tools call this."""
        slug = RunConfig.from_runtime().workspace_slug
        if slug is None:
            raise LookupError("A curator's run names its workspace")
        return cls(slug)

    @property
    def thread_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"workspace:{self.slug}:curator"))

    async def wake(self) -> str:
        """Start a run on the curator's thread, creating the thread the first time; a run
        already going keeps going, and this one waits behind it."""
        client = dispatch_client()
        thread_id = await succession.succeed(self.thread_id)
        await create_thread(
            client,
            thread_id,
            title=f"Engram curator: {self.slug}",
            if_exists="do_nothing",
            metadata={
                "owner_type": "system",
                "visibility": "public",
                "workspace": self.slug,
                CURATOR_THREAD_KEY: self.slug,
            },
        )
        configurable = {"thread_id": thread_id, "workspace": self.slug, "source": CURATOR_SOURCE}
        run = await create_durable_run(
            thread_id,
            _ASSISTANT_ID,
            input=build_run_input(
                prompt("curator/wake", workspace=self.slug),
                {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
                systems=[
                    {"id": _SENDER_ID, "display_name": "Engram curator", "platform": "open-swe"}
                ],
            ),
            source=CURATOR_SOURCE,
            thread_title=None,
            config={"configurable": with_invocation_id(configurable, new_invocation_id())},
            client=client,
            multitask_strategy="enqueue",
        )
        return str(run["run_id"])

    @classmethod
    async def wake_pending(cls, job: Job) -> None:
        """Wake the curator of every workspace with an engram it has not decided."""
        # Once per fire: a job claimed again after a deploy or crash may have woken them.
        if not await event_claims.claim(WAKE_JOB, job.due_at.isoformat(), ttl=_FIRE_CLAIM_TTL):
            return
        for slug in await ENGRAMS.pending_workspaces():
            try:
                run_id = await cls(slug).wake()
            except Exception:
                logger.exception("Could not wake a workspace's curator", extra={"workspace": slug})
                continue
            logger.info("Woke a workspace's curator", extra={"workspace": slug, "run_id": run_id})
