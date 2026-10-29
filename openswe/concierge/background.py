"""A person's concierge: their DM conversation, and the background thread that works for it.

The background thread is one dashboard thread per person, woken on a timer with
their standing instructions so its conversation, and the model's prompt cache,
carry over between check-ins. It shares the DM thread's sandbox and never posts in
Slack: what the person should hear goes to the DM agent, which owns the DM.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph_sdk.errors import NotFoundError

from openswe.concierge.records import ConciergeBackground
from openswe.input_messages import SystemIdentity
from openswe.prompts import prompt
from openswe.sandboxes.tool_access import SANDBOX_HOST_THREAD_KEY
from openswe.slack.dm import CONCIERGE_TS, open_dm
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.slack.thread_owner import wake_thread_owner
from openswe.tasks.schemas import CONCIERGE_BACKGROUND_KEY, ThreadMetadata
from openswe.threads import index
from openswe.threads.runs import create_system_thread, run_on_system_message
from openswe.users import User
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_reads import read_thread

logger = logging.getLogger(__name__)

_CHECK_IN: SystemIdentity = {
    "id": "system:concierge-check-in",
    "display_name": "Concierge check-in",
    "platform": "open-swe",
}
DEFAULT_INSTRUCTIONS = prompt("concierge/default-instructions")


@dataclass(frozen=True)
class Concierge:
    user: User
    dm_channel_id: str
    dm_thread_id: str

    @classmethod
    async def of(cls, user: User) -> Self | None:
        """The person's concierge; ``None`` until they have a concierge DM conversation."""
        if not user.typed_preferences.concierge_mode or not user.slack_user_id:
            return None
        channel_id = await open_dm(user.slack_user_id)
        thread_id = await lookup_slack_thread_id(channel_id, CONCIERGE_TS) if channel_id else None
        if channel_id is None or thread_id is None:
            return None
        return cls(user=user, dm_channel_id=channel_id, dm_thread_id=thread_id)

    @classmethod
    async def for_background_thread(cls, thread_id: str) -> Self | None:
        """The concierge ``thread_id`` works for in the background, if it is such a thread."""
        row = await ConciergeBackground.for_thread(thread_id)
        user = await User.get(row.user_id) if row is not None else None
        return await cls.of(user) if user is not None else None

    async def check_in(self) -> bool:
        """Wake the background thread with the person's standing instructions; whether it ran.

        A running thread is left alone. One that is gone, or that shares a DM thread
        the person no longer talks in, is replaced.
        """
        login = self.user.github_login
        if not login:
            return False
        row = await ConciergeBackground.for_user(self.user.id)
        text = prompt("concierge/check-in", **self._check_in_values(row))
        if row is not None and row.thread_id is not None:
            if row.dm_thread_id == self.dm_thread_id:
                thread = await index.get(row.thread_id)
                if thread is not None and thread.status == "busy":
                    return False
                try:
                    await run_on_system_message(row.thread_id, login, text, _CHECK_IN)
                    return True
                except NotFoundError:
                    logger.info(
                        "Concierge background thread is gone; starting another",
                        extra={"agent_thread_id": row.thread_id},
                    )
        await self._start_background_thread(login, text)
        return True

    async def tell(self, text: str, *, wake: bool) -> None:
        """Pass the background thread's report to the DM agent, starting a run when ``wake``."""
        event = prompt("concierge/background-report", text=text)
        if wake:
            await wake_thread_owner(
                self.dm_channel_id, CONCIERGE_TS, self.user.slack_user_id, event
            )
        else:
            await note_for_thread_owner(self.dm_channel_id, CONCIERGE_TS, event)

    async def _start_background_thread(self, login: str, check_in: str) -> None:
        host = ThreadMetadata.model_validate(thread_metadata(await read_thread(self.dm_thread_id)))
        thread_id = await create_system_thread(
            login,
            self.user.email or None,
            title=f"Concierge background for {self.user.login_for('slack')}",
            text=check_in,
            visibility=host.visibility,
            workspace=host.workspace or host.environment,
            extra_metadata={
                SANDBOX_HOST_THREAD_KEY: self.dm_thread_id,
                CONCIERGE_BACKGROUND_KEY: True,
                "admin_thread": host.admin_thread,
            },
        )
        # Recorded before the first run, which checks it to share the DM thread's sandbox.
        await ConciergeBackground.bind_threads(self.user.id, thread_id, self.dm_thread_id)
        opening = prompt("concierge/background-start", check_in=check_in)
        await run_on_system_message(thread_id, login, opening, _CHECK_IN)
        logger.info(
            "Started a concierge background thread",
            extra={"agent_thread_id": thread_id, "dm_thread_id": self.dm_thread_id},
        )

    def _check_in_values(self, row: ConciergeBackground | None) -> dict[str, str]:
        timezone = self.user.slack_timezone
        return {
            "instructions": (row.instructions if row is not None else "") or DEFAULT_INSTRUCTIONS,
            "timezone": timezone or "UTC",
            "local_time": datetime.now(_zone(timezone)).strftime("%A %Y-%m-%d %H:%M %Z"),
        }


def _zone(timezone: str) -> tzinfo:
    if not timezone:
        return UTC
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError, ValueError:
        logger.warning("Unknown Slack timezone; using UTC", extra={"slack_timezone": timezone})
        return UTC
