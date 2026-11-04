"""Connect cards for a person's managed tools, and the follow-up run once they work.

The agent offers a card in a private thread for the LMT services the owner asked to use
and has not connected. Each button opens Open SWE, which mints LMT's single-use consent
link only for the card's owner, waits for that consent to finish, and queues one
follow-up run once the card's services work. Waiting happens in the dashboard process
that minted the link; if it restarts, the person says "continue".
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import datetime, timedelta
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
from openswe.mcp.managed import (
    BotTools,
    ConsentLink,
    ManagedTools,
    ManagedToolsError,
    PersonalTools,
)
from openswe.prompts import prompt
from openswe.source_context import SourceContext
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
_waiters: set[asyncio.Task[None]] = set()


def _wait_in_background(waiting: Coroutine[object, object, None]) -> None:
    task = asyncio.create_task(waiting)
    _waiters.add(task)
    task.add_done_callback(_waiters.discard)


async def _consented(waiter: PersonalTools, refreshed: ManagedTools, auth_id: str) -> bool:
    """Wait out one consent link as ``waiter``; once used, re-list ``refreshed`` so the
    new service's tools load."""
    outcome = await waiter.consent_outcome(auth_id)
    if outcome != "completed":
        logger.info("Managed tools consent not completed", extra={"outcome": outcome})
        return False
    try:
        await refreshed.refresh()
    except Exception:
        # Best effort: a stale list expires on its own; it may not stop the thread resuming.
        logger.warning("Managed tools refresh after consent failed", exc_info=True)
    return True


async def _refresh_after(waiter: PersonalTools, refreshed: ManagedTools, auth_id: str) -> None:
    try:
        await _consented(waiter, refreshed, auth_id)
    except TimeoutError:
        logger.info("Stopped waiting for managed tools consent")
    except Exception:
        logger.warning("Managed tools refresh after consent failed", exc_info=True)


async def connect_service(login: str, server_id: str) -> ConsentLink | None:
    """A consent link for one of a person's services outside any thread."""
    tools = PersonalTools(login)
    link = await tools.connect_link(server_id)
    if link is not None and link.auth_id:
        _wait_in_background(_refresh_after(tools, tools, link.auth_id))
    return link


async def connect_bot_service(admin: str, bot: BotTools, server_id: str) -> ConsentLink:
    """A consent link where an admin connects one kit service for a workspace bot."""
    tools = PersonalTools(admin)
    link = await tools.connect_for_bot(bot, server_id)
    if link.auth_id:
        _wait_in_background(_refresh_after(tools, bot, link.auth_id))
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
            _wait_in_background(self._resume_after(link.auth_id))
        return link

    async def _resume_after(self, auth_id: str) -> None:
        extra = {"agent_thread_id": self.thread_id, "card": self.card_id}
        try:
            if not await _consented(self.tools, self.tools, auth_id):
                return
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
        except TimeoutError:
            logger.info("Stopped waiting for managed tools consent", extra=extra)
        except ManagedToolsError:
            logger.warning("Managed tools consent check failed", extra=extra, exc_info=True)
        except Exception:
            logger.exception("Could not resume thread after managed tools consent", extra=extra)

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
