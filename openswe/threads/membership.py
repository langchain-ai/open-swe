"""Owner-approved participation, independent of a thread's visibility."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import HTTPException

from openswe.ui_invalidations import Topic
from openswe.utils.thread_ops import langgraph_client

ADMITTED_PREFIX = "thread_admitted:"
JOIN_REQUEST_PREFIX = "thread_join_requested:"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadMembership:
    thread_id: str
    metadata: Mapping[str, object]

    @property
    def owner(self) -> str | None:
        value = self.metadata.get("owner_login")
        return value.strip().lower() if isinstance(value, str) and value.strip() else None

    @property
    def requires_approval(self) -> bool:
        return self.owner is not None and self.metadata.get("visibility", "public") == "public"

    def is_owner(self, login: str | None) -> bool:
        return self.owner is not None and self.owner == (login or "").strip().lower()

    def admits(self, login: str | None) -> bool:
        if self.is_owner(login):
            return True
        if self.metadata.get("visibility", "public") != "public":
            return False
        if not self.requires_approval:
            return True
        return (
            login is not None and self.metadata.get(ADMITTED_PREFIX + login.strip().lower()) is True
        )

    def pending(self, login: str) -> bool:
        return (
            not self.admits(login)
            and self.metadata.get(JOIN_REQUEST_PREFIX + login.strip().lower()) is True
        )

    def view(self, login: str) -> dict[str, object]:
        return {
            "ownerLogin": self.owner,
            "requiresApproval": self.requires_approval,
            "canParticipate": self.admits(login),
            "requestPending": self.pending(login),
            "pendingLogins": sorted(
                key.removeprefix(JOIN_REQUEST_PREFIX)
                for key, value in self.metadata.items()
                if self.is_owner(login)
                and key.startswith(JOIN_REQUEST_PREFIX)
                and value is True
                and self.pending(key.removeprefix(JOIN_REQUEST_PREFIX))
            ),
        }

    @classmethod
    async def read(cls, thread_id: str, login: str, email: str | None = None) -> ThreadMembership:
        from openswe.threads.access import _readable_thread_metadata

        return cls(thread_id, await _readable_thread_metadata(thread_id, login=login, email=email))

    async def request(self, login: str) -> bool:
        if not self.requires_approval:
            raise HTTPException(409, "this thread does not accept join requests")
        if self.admits(login) or self.pending(login):
            return False
        await self._update({JOIN_REQUEST_PREFIX + login.strip().lower(): True})
        await self._notify_owner(login)
        return True

    async def _notify_owner(self, login: str) -> None:
        from openswe.slack.dm import DmOrigin, send_dm
        from openswe.source_context import SourceContext
        from openswe.users import User
        from openswe.utils.dashboard_links import dashboard_thread_url

        try:
            owner = await User.for_login("github", self.owner or "")
            url = dashboard_thread_url(self.thread_id)
            if owner is None or not owner.slack_user_id or not url:
                return
            slack = SourceContext.from_metadata(self.metadata).slack_thread
            await send_dm(
                owner.slack_user_id,
                f"{login} requested to join your thread. Approve or deny their request in <{url}|the dashboard>. They can still view it while waiting.",
                origin=DmOrigin(channel_id=slack.channel_id, thread_ts=slack.thread_ts)
                if slack
                else None,
            )
        except Exception:
            logger.warning(
                "Could not notify the thread owner of a join request",
                extra={"agent_thread_id": self.thread_id},
                exc_info=True,
            )

    async def decide(self, owner_login: str, applicant: str, *, approve: bool) -> None:
        if not self.is_owner(owner_login):
            raise HTTPException(403, "only the thread owner can approve join requests")
        if not self.requires_approval:
            raise HTTPException(409, "this thread does not accept join requests")
        applicant = applicant.strip().lower()
        if not self.pending(applicant):
            raise HTTPException(409, "this person has no pending join request")
        patch = {JOIN_REQUEST_PREFIX + applicant: False}
        if approve:
            patch[ADMITTED_PREFIX + applicant] = True
        await self._update(patch)

    async def _update(self, patch: dict[str, bool]) -> None:
        from openswe.transcript.mirror import mirror_thread_metadata

        await langgraph_client().threads.update(thread_id=self.thread_id, metadata=patch)
        await mirror_thread_metadata(self.thread_id, patch)
        await Topic.THREAD_MEMBERSHIPS.invalidate(key=self.thread_id)


async def read_thread_membership(
    thread_id: str, login: str, email: str | None = None
) -> dict[str, object]:
    return (await ThreadMembership.read(thread_id, login, email)).view(login)
