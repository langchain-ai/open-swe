"""Connect cards for a person's managed tools, and the follow-up run once they work.

The agent offers a card in a private thread for the LMT services the owner asked to use
and has not connected. Each button opens Robin, which mints LMT's single-use consent
link only for the card's owner, waits for that consent to finish, and queues one
follow-up run once the card's services work. The job queue does the waiting, one long
poll per job, so a restarted or replaced backend only delays the follow-up run.
"""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Text, select
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.dispatch import dispatch_agent_run, follow_up_configurable
from openswe.event_claims import claim, release
from openswe.input_messages import InputMessageContext, SystemIdentity
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.mcp.managed import (
    ConsentLink,
    ManagedToolsError,
    PersonalTools,
)
from openswe.prompts import prompt
from openswe.source_context import SourceContext
from openswe.users.models import User
from openswe.users.records import UnknownUser, user_id_for_login
from openswe.utils.dashboard_links import dashboard_api_base_url
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_RESUME_SCOPE = "managed_tools_resume"
_SENDER: SystemIdentity = {
    "id": "system:managed-tools",
    "display_name": "Managed tools",
    "platform": "open-swe",
}
_CONTEXT: InputMessageContext = {
    "sender_id": _SENDER["id"],
    "surface": "automation",
    "kind": "system",
}
CONSENT_JOB = "managed_tools_consent"
# Consent links expire after ten minutes; stop waiting shortly after.
_CONSENT_DEADLINE = timedelta(minutes=11)
# LangSmith's long poll sometimes returns early; never ask it again sooner than this.
_CONSENT_POLL_FLOOR = timedelta(seconds=1)


class ConsentWait(BaseModel):
    """One consent link a person was sent, waited on through the job queue: each job long
    polls LangSmith once, then queues the next until the link is used or expires."""

    login: str
    """The person's ``user:<id>`` key, which outlives a GitHub login rename; a job
    queued before keys were stored holds the login itself."""
    auth_id: str
    until: datetime
    thread_id: str | None = None
    """The thread whose connect card sent the link, which resumes once the card's
    services work."""
    card_id: str | None = None

    @classmethod
    async def start(cls, login: str, auth_id: str, card: ConnectCard | None = None) -> None:
        if not login.startswith("user:"):
            user = await User.for_login("github", login)
            if user is None:
                raise UnknownUser(login)
            login = f"user:{user.id}"
        wait = cls(
            login=login,
            auth_id=auth_id,
            until=datetime.now(UTC) + _CONSENT_DEADLINE,
            thread_id=card.thread_id if card else None,
            card_id=card.card_id if card else None,
        )
        await wait.schedule(datetime.now(UTC))

    async def schedule(self, due_at: datetime) -> None:
        await queue.schedule(CONSENT_JOB, self.model_dump(mode="json"), due_at=due_at)

    @classmethod
    async def run(cls, job: Job) -> None:
        await cls.model_validate(job.payload).poll()

    async def poll(self) -> None:
        login = self.login
        if login.startswith("user:"):
            user = await User.for_login("github", login)
            if user is None:
                logger.info("Managed tools consent owner no longer exists")
                return
            # Runs name a GitHub-linked person by their current login.
            login = user.github_login or login
        tools = PersonalTools(login)
        status = await tools.consent_status(self.auth_id)
        if status == "pending":
            if datetime.now(UTC) < self.until:
                await self.schedule(datetime.now(UTC) + _CONSENT_POLL_FLOOR)
            else:
                logger.info("Stopped waiting for managed tools consent")
            return
        if status != "completed":
            logger.info("Managed tools consent not completed", extra={"outcome": status})
            return
        try:
            await tools.refresh()
        except Exception:
            # Best effort: a stale list expires on its own; it may not stop the thread resuming.
            logger.warning("Managed tools refresh after consent failed", exc_info=True)
        if self.thread_id is None or self.card_id is None:
            return
        card = await ConnectCard.load(self.thread_id, self.card_id, login)
        if card is not None:
            await card.resume_if_ready()


async def connect_service(login: str, server_id: str) -> ConsentLink | None:
    """A consent link for one of a person's services outside any thread."""
    link = await PersonalTools(login).connect_link(server_id)
    if link is not None and link.auth_id:
        await ConsentWait.start(login, link.auth_id)
    return link


class ConnectCardRow(Base):
    __tablename__ = "managed_tools_card"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    card_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    services: Mapped[list[str]] = mapped_column(ARRAY(Text), default_factory=list)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class ConnectCard(BaseModel):
    """A card the agent offered one person in a thread, for some of their LMT services."""

    thread_id: str
    card_id: str
    login: str
    services: list[str] = Field(default_factory=list)
    """IDs of the LMT servers the card offers."""

    @classmethod
    async def load(cls, thread_id: str, card_id: str, login: str) -> ConnectCard | None:
        """The card if the agent offered it to ``login``, else ``None``."""
        async with postgres.session() as session:
            row = await session.scalar(
                select(ConnectCardRow).where(
                    ConnectCardRow.thread_id == thread_id,
                    ConnectCardRow.card_id == card_id,
                    ConnectCardRow.user_id == user_id_for_login(login),
                )
            )
        if row is None:
            return None
        return cls(
            thread_id=row.thread_id,
            card_id=row.card_id,
            login=login.strip().lower(),
            services=row.services,
        )

    @property
    def tools(self) -> PersonalTools:
        return PersonalTools(self.login)

    async def save(self) -> None:
        async with postgres.session() as session:
            user_id = await session.scalar(select(user_id_for_login(self.login)))
            if user_id is None:
                raise UnknownUser(self.login)
            values = {"user_id": user_id, "services": self.services}
            await session.execute(
                insert(ConnectCardRow)
                .values(thread_id=self.thread_id, card_id=self.card_id, **values)
                .on_conflict_do_update(
                    index_elements=[ConnectCardRow.thread_id, ConnectCardRow.card_id],
                    set_=values,
                )
            )

    def button_url(self, server_id: str) -> str:
        """Where a Slack button sends the person: Open SWE, never LMT's link itself."""
        path = f"/dashboard/api/my-managed-tools/connect/{quote(server_id, safe='')}"
        query = urlencode({"thread_id": self.thread_id, "card": self.card_id})
        return f"{dashboard_api_base_url()}{path}?{query}"

    async def connect(self, server_id: str) -> ConsentLink | None:
        """Mint a consent link for one of the card's services and resume the thread once
        it is used."""
        if server_id not in self.services:
            raise ManagedToolsError("This card does not offer that service")
        link = await self.tools.connect_link(server_id)
        if link is not None and link.auth_id:
            await ConsentWait.start(self.login, link.auth_id, self)
        return link

    async def resume_if_ready(self) -> None:
        """Queue the thread's one follow-up run once every service the card offers works."""
        # Another of the card's services may still be missing; its consent resumes.
        servers = {server.id: server for server in await self.tools.servers()}
        if any(not servers[service].usable for service in self.services if service in servers):
            return
        names = [servers[service].name for service in self.services if service in servers]
        key = f"{self.thread_id}:{self.card_id}"
        if not await claim(_RESUME_SCOPE, key, ttl=timedelta(days=7)):
            return
        try:
            await self._resume(names)
        except BaseException:
            await release(_RESUME_SCOPE, key)
            raise

    async def _resume(self, services: list[str]) -> None:
        thread = await langgraph_client().threads.get(self.thread_id)
        metadata = thread_metadata(thread)
        configurable = follow_up_configurable(metadata, self.thread_id)
        # Only the card's owner can use their own services, so the run is theirs.
        configurable["github_login"] = self.login
        await dispatch_agent_run(
            self.thread_id,
            prompt("runs/managed-tools-connected", services=", ".join(services)),
            configurable,
            source=str(configurable.get("source") or "dashboard"),
            thread_title=None,
            context=_CONTEXT,
            systems=[_SENDER],
            metadata={},
            multitask_strategy="enqueue",
            source_context=SourceContext.from_metadata(metadata),
        )
