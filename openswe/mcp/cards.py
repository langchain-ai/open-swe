"""Connect cards for a workspace's managed tools, and the follow-up run once they work.

The agent offers a card in a private thread for the services the owner asked to use
and has not connected in the workspace's LMT gateway (every unconnected service, when
the gateway offers no tools until all are connected). Each button opens Open SWE, which
mints LMT's single-use consent link only for the card's owner, waits for that consent to
finish, and queues one follow-up run once the card's services work. Waiting happens in
the dashboard process that minted the link; if it restarts, the person says "continue".
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import timedelta
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from openswe.dispatch import dispatch_agent_run, follow_up_configurable
from openswe.event_claims import claim, release
from openswe.input_messages import InputMessageContext, SystemIdentity
from openswe.mcp.managed import (
    ConsentLink,
    GatewayStatus,
    ManagedToolsError,
    connect_link,
    consent_outcome,
    gateway_id,
    gateway_status,
    refresh_tools,
)
from openswe.prompts import prompt
from openswe.source_context import SourceContext
from openswe.utils.dashboard_links import dashboard_api_base_url
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_CARDS_NAMESPACE = "managed_tools_cards"
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


async def _consented(login: str, gateway: str, auth_id: str) -> bool:
    """Wait out one consent link; once used, re-list the gateway so new tools load."""
    outcome = await consent_outcome(login, auth_id)
    if outcome != "completed":
        logger.info("Managed tools consent not completed", extra={"outcome": outcome})
        return False
    try:
        await refresh_tools(login, gateway)
    except Exception:
        # Best effort: a gateway still missing services refuses to list, and a stale
        # list expires on its own; neither may stop the thread from resuming.
        logger.warning("Managed tools refresh after consent failed", exc_info=True)
    return True


async def _refresh_after(login: str, gateway: str, auth_id: str) -> None:
    try:
        await _consented(login, gateway, auth_id)
    except TimeoutError:
        logger.info("Stopped waiting for managed tools consent", extra={"gateway": gateway})
    except Exception:
        logger.warning(
            "Managed tools refresh after consent failed", extra={"gateway": gateway}, exc_info=True
        )


async def connect_service(login: str, gateway: str, slug: str) -> ConsentLink | None:
    """A consent link for one service outside any thread; its tools load once it is used."""
    link = await connect_link(login, gateway, slug)
    if link is not None and link.auth_id:
        _wait_in_background(_refresh_after(login, gateway, link.auth_id))
    return link


class ConnectCard(BaseModel):
    """A card the agent offered one person in a thread, for one gateway."""

    thread_id: str
    card_id: str
    login: str
    gateway: str
    services: list[str] = Field(default_factory=list)
    """Slugs of the services the card offers; empty offers every unconnected one."""

    @classmethod
    async def load(cls, thread_id: str, card_id: str) -> ConnectCard | None:
        item = await langgraph_client().store.get_item((_CARDS_NAMESPACE, thread_id), card_id)
        value = item.get("value") if item else None
        return cls.model_validate(value) if isinstance(value, dict) else None

    async def save(self) -> None:
        await langgraph_client().store.put_item(
            (_CARDS_NAMESPACE, self.thread_id), self.card_id, self.model_dump()
        )

    def owned_by(self, login: str, gateway: str) -> bool:
        return self.login.lower() == login.lower() and self.gateway == gateway_id(gateway)

    def button_url(self, slug: str) -> str:
        """Where a Slack button sends the person: Open SWE, never LMT's link itself."""
        path = f"/dashboard/api/my-managed-tools/{self.gateway}/connect/{quote(slug, safe='')}"
        query = urlencode({"thread_id": self.thread_id, "card": self.card_id})
        return f"{dashboard_api_base_url()}{path}?{query}"

    async def connect(self, slug: str) -> ConsentLink | None:
        """Mint a consent link for ``slug`` and resume the thread once it is used."""
        link = await connect_link(self.login, self.gateway, slug)
        if link is not None and link.auth_id:
            _wait_in_background(self._resume_after(link.auth_id))
        return link

    async def _resume_after(self, auth_id: str) -> None:
        extra = {"agent_thread_id": self.thread_id, "card": self.card_id}
        try:
            if not await _consented(self.login, self.gateway, auth_id):
                return
            # Another of the card's services may still be missing; its consent resumes.
            status = await gateway_status(self.login, self.gateway)
            if status.missing_for(self.services):
                return
            key = f"{self.thread_id}:{self.card_id}"
            if not await claim(_RESUME_SCOPE, key, ttl=timedelta(days=7)):
                return
            try:
                await self._resume(status)
            except BaseException:
                await release(_RESUME_SCOPE, key)
                raise
        except TimeoutError:
            logger.info("Stopped waiting for managed tools consent", extra=extra)
        except ManagedToolsError:
            logger.warning("Managed tools consent check failed", extra=extra, exc_info=True)
        except Exception:
            logger.exception("Could not resume thread after managed tools consent", extra=extra)

    async def _resume(self, status: GatewayStatus) -> None:
        thread = await langgraph_client().threads.get(self.thread_id)
        metadata = thread_metadata(thread)
        configurable = follow_up_configurable(metadata, self.thread_id)
        # Only the card's owner can see the gateway's tools, so the run is theirs.
        configurable["github_login"] = self.login
        await dispatch_agent_run(
            self.thread_id,
            prompt("runs/managed-tools-connected", gateway=status.gateway.name),
            configurable,
            source=str(configurable.get("source") or "dashboard"),
            thread_title=None,
            context=_CONTEXT,
            systems=[_SENDER],
            metadata={},
            multitask_strategy="enqueue",
            source_context=SourceContext.from_metadata(metadata),
        )
