"""Requests to act as a thread participant, one per thread and person, in thread metadata."""

import hashlib
import logging
from typing import Literal, Self

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, ValidationError

from agent.store import now_iso
from agent.users import User, UserPreferencesPatch
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY, participant_logins

logger = logging.getLogger(__name__)

ACT_AS_KEY = "act_as_requests"
_MAX_REQUESTS = 20

ActAsStatus = Literal["pending", "approved", "denied"]


class ActAsRequest(BaseModel):
    """Open SWE asking one person to let it open PRs as them in one thread."""

    model_config = ConfigDict(extra="ignore")

    fingerprint: str
    login: str
    status: ActAsStatus = "pending"
    owner: str = ""
    repo: str = ""
    head: str = ""
    base: str = ""
    title: str = ""
    requested_at: str = ""
    notified: bool = False
    decided_at: str | None = None

    @staticmethod
    def fingerprint_for(thread_id: str, login: str) -> str:
        return hashlib.sha256(f"{thread_id}|{login.lower()}".encode()).hexdigest()[:16]


class ThreadActAs:
    """The act-as requests and participants of one thread."""

    def __init__(
        self, thread_id: str, requests: dict[str, ActAsRequest], participants: list[str]
    ) -> None:
        self.thread_id = thread_id
        self.requests = requests
        self.participants = participants

    @classmethod
    async def load(cls, thread_id: str) -> Self:
        thread = await get_client().threads.get(thread_id)
        metadata = thread["metadata"] or {}
        stored = metadata.get(ACT_AS_KEY)
        requests: dict[str, ActAsRequest] = {}
        for fingerprint, value in (stored if isinstance(stored, dict) else {}).items():
            try:
                requests[fingerprint] = ActAsRequest.model_validate(value)
            except ValidationError:
                logger.warning(
                    "Dropping an unreadable act-as request",
                    extra={"thread_id": thread_id, "fingerprint": fingerprint},
                    exc_info=True,
                )
        return cls(thread_id, requests, participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY)))

    @property
    def is_shared(self) -> bool:
        return len(self.participants) > 1

    def for_login(self, login: str) -> ActAsRequest | None:
        return self.requests.get(ActAsRequest.fingerprint_for(self.thread_id, login))

    async def request(
        self, login: str, *, owner: str, repo: str, head: str, base: str, title: str
    ) -> ActAsRequest:
        """The open request for ``login``, created on first ask."""
        existing = self.for_login(login)
        if existing is not None:
            return existing
        request = ActAsRequest(
            fingerprint=ActAsRequest.fingerprint_for(self.thread_id, login),
            login=login,
            owner=owner,
            repo=repo,
            head=head,
            base=base,
            title=title,
            requested_at=now_iso(),
        )
        while len(self.requests) >= _MAX_REQUESTS:
            self.requests.pop(next(iter(self.requests)))
        self.requests[request.fingerprint] = request
        await self._save()
        return request

    async def mark_notified(self, request: ActAsRequest) -> None:
        request.notified = True
        await self._save()

    async def decide(self, request: ActAsRequest, *, approved: bool, always_allow: bool) -> None:
        request.status = "approved" if approved else "denied"
        request.decided_at = now_iso()
        await self._save()
        if always_allow:
            patch = UserPreferencesPatch(act_as_always_allowed=True)
            if await User.update_preferences(request.login, patch) is None:
                logger.warning(
                    "No user row to store the act-as preference on",
                    extra={"login": request.login},
                )

    async def _save(self) -> None:
        stored = {fingerprint: r.model_dump() for fingerprint, r in self.requests.items()}
        await get_client().threads.update(thread_id=self.thread_id, metadata={ACT_AS_KEY: stored})
