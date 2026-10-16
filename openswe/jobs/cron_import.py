"""Move the scheduler's Agent Server crons onto the job queue, once.

Each cron becomes the job its owner would create today, its owner's record is
pointed at that job, and the cron is deleted. A cron whose owner is gone (a
stopped watch, a removed trigger) and the kinds nothing runs any more are only
deleted. It runs at startup; once every cron has moved, it is a single empty search.
"""

import logging
from datetime import timedelta
from typing import Final

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, JsonValue

from openswe import event_claims
from openswe.baby_sit import WATCH_CRON_KIND
from openswe.baby_sit import adopt_agent_server_cron as adopt_watch
from openswe.background_tasks import CRON_KIND as BACKGROUND_TASKS
from openswe.background_tasks import ensure_background_task_polling
from openswe.schedules.store import adopt_agent_server_cron as adopt_trigger
from openswe.workspaces.refresh import LEGACY_REFRESH_TASK, REFRESH_TASK
from openswe.workspaces.refresh import adopt_agent_server_cron as adopt_refresh

logger = logging.getLogger(__name__)

_PAGE: Final = 100
# Kinds whose crons nothing needs: retired features and one-time cleanups.
_RETIRED: Final = frozenset({"expedited_review", "analyzer_continual", "thread_feedback"})


class _AgentServerCron(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cron_id: str
    schedule: str = ""
    metadata: dict[str, JsonValue] = {}
    payload: dict[str, JsonValue] = {}

    def field(self, name: str) -> str | None:
        """``name`` from the run input the cron sends, else from its metadata."""
        given = self.payload.get("input")
        value = given.get(name) if isinstance(given, dict) else None
        if value is None:
            value = self.metadata.get(name)
        return value if isinstance(value, str) and value else None

    @property
    def workspace_slug(self) -> str | None:
        """The workspace a refresh cron rebuilds; crons from before the rename say environment."""
        names = ("workspace_slug", "environment_slug", "workspace", "environment")
        return next((value for name in names if (value := self.field(name))), None)

    @property
    def kind(self) -> str | None:
        return self.field("task") or self.field("kind")

    async def adopt(self) -> bool:
        """Create this cron's job; ``False`` when nothing should replace it."""
        kind = self.kind
        if kind in _RETIRED:
            return False
        if kind in {"baby_sit", WATCH_CRON_KIND} and (key := self.field("watch_key")):
            return await adopt_watch(key)
        if kind == BACKGROUND_TASKS and (thread_id := self.field("thread_id")):
            await ensure_background_task_polling(thread_id)
            return True
        if kind in {REFRESH_TASK, LEGACY_REFRESH_TASK} and (slug := self.workspace_slug):
            return await adopt_refresh(slug, self.cron_id)
        if automation_id := self.field("schedule_id"):
            return await adopt_trigger(
                self.cron_id,
                automation_id,
                self.field("trigger_id"),
                self.schedule,
                self.field("created_by"),
            )
        raise LookupError(f"No job replaces scheduler crons of kind {kind}")


async def import_agent_server_crons() -> int:
    """Move every scheduler cron onto the job queue; how many became jobs."""
    if not await event_claims.claim("jobs", "agent-server-cron-import", ttl=timedelta(minutes=10)):
        return 0
    client = get_client()
    crons: list[_AgentServerCron] = []
    offset = 0
    # Searched without an assistant: the Agent Server only filters by assistant UUID.
    while page := await client.crons.search(limit=_PAGE, offset=offset):
        offset += len(page)
        # A thread-bound cron is a thread wakeup, which fires once and goes on its own.
        crons.extend(
            _AgentServerCron.model_validate(dict(cron))
            for cron in page
            if not cron.get("thread_id")
        )
        if len(page) < _PAGE:
            break
    adopted = 0
    for cron in crons:
        try:
            adopted += await cron.adopt()
        except Exception:
            # Left in place: it keeps firing as before, and the next startup tries again.
            logger.exception(
                "Could not move a scheduler cron to the job queue",
                extra={"cron_id": cron.cron_id, "cron_kind": cron.kind},
            )
            continue
        try:
            await client.crons.delete(cron.cron_id)
        except Exception:
            # Moved already; the next startup finds it adopted and deletes it then.
            logger.warning(
                "Could not delete a moved scheduler cron",
                extra={"cron_id": cron.cron_id},
                exc_info=True,
            )
    if crons:
        logger.info(
            "Moved scheduler crons to the job queue",
            extra={"cron_count": len(crons), "adopted_count": adopted},
        )
    return adopted
