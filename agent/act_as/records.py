"""Requests to act as a thread participant, one per thread and person, in thread metadata.

Each request and each decision is its own top-level metadata key: thread metadata
updates merge per key, so a stale request write can never erase a decision, and
two people's requests never overwrite each other.
"""

import hashlib
import logging
from collections.abc import Mapping
from typing import Literal, Self

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, ValidationError

from agent.slack.events import claim_slack_event
from agent.store import now_iso
from agent.users import User, UserPreferencesPatch
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    participant_logins,
)

logger = logging.getLogger(__name__)

_REQUEST_PREFIX = "act_as_request:"
_DECISION_PREFIX = "act_as_decision:"

Decision = Literal["approved", "denied"]


class ActAsRequest(BaseModel):
    """Open SWE asking one person to let it open PRs as them in one thread."""

    model_config = ConfigDict(extra="ignore")

    fingerprint: str
    login: str
    owner: str = ""
    repo: str = ""
    head: str = ""
    base: str = ""
    title: str = ""
    requested_at: str = ""
    notified: bool = False
    wake_on_answer: bool = False

    @staticmethod
    def fingerprint_for(thread_id: str, login: str) -> str:
        return hashlib.sha256(f"{thread_id}|{login.lower()}".encode()).hexdigest()[:16]


class ActAsDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: Decision
    decided_at: str


class ThreadActAs:
    """The act-as requests, decisions and participants of one thread."""

    def __init__(
        self,
        thread_id: str,
        requests: dict[str, ActAsRequest],
        decisions: dict[str, ActAsDecision],
        participant_count: int,
    ) -> None:
        self.thread_id = thread_id
        self.requests = requests
        self.decisions = decisions
        self.participant_count = participant_count

    @classmethod
    async def load(cls, thread_id: str) -> Self:
        thread = await get_client().threads.get(thread_id)
        metadata = thread["metadata"] or {}
        requests = _parse(thread_id, metadata, _REQUEST_PREFIX, ActAsRequest)
        decisions = _parse(thread_id, metadata, _DECISION_PREFIX, ActAsDecision)
        # Senders with no GitHub link are tracked by email only, and can steer the run too.
        participant_count = len(participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY))) + len(
            participant_logins(metadata.get(PARTICIPANT_EMAILS_KEY))
        )
        return cls(thread_id, requests, decisions, participant_count)

    @property
    def is_shared(self) -> bool:
        return self.participant_count > 1

    def for_login(self, login: str) -> ActAsRequest | None:
        return self.requests.get(ActAsRequest.fingerprint_for(self.thread_id, login))

    def decision_for(self, login: str) -> Decision | None:
        decision = self.decisions.get(ActAsRequest.fingerprint_for(self.thread_id, login))
        return decision.decision if decision is not None else None

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
        self.requests[request.fingerprint] = request
        await self._update({_REQUEST_PREFIX + request.fingerprint: request.model_dump()})
        return request

    async def mark_notified(self, request: ActAsRequest) -> None:
        request.notified = True
        await self._update({_REQUEST_PREFIX + request.fingerprint: request.model_dump()})

    async def set_wake_on_answer(self, request: ActAsRequest, wake: bool) -> None:
        request.wake_on_answer = wake
        await self._update({_REQUEST_PREFIX + request.fingerprint: request.model_dump()})

    async def decide(self, request: ActAsRequest, *, approved: bool, always_allow: bool) -> bool:
        """Record the first answer; ``False`` when the request was already answered."""
        if request.fingerprint in self.decisions or not await claim_slack_event(
            f"act-as-decision:{self.thread_id}:{request.fingerprint}"
        ):
            return False
        decision = ActAsDecision(
            decision="approved" if approved else "denied", decided_at=now_iso()
        )
        self.decisions[request.fingerprint] = decision
        await self._update({_DECISION_PREFIX + request.fingerprint: decision.model_dump()})
        if always_allow:
            patch = UserPreferencesPatch(act_as_always_allowed=True)
            if await User.update_preferences(request.login, patch) is None:
                logger.warning(
                    "No user row to store the act-as preference on",
                    extra={"login": request.login},
                )
        return True

    async def _update(self, metadata: dict[str, object]) -> None:
        await get_client().threads.update(thread_id=self.thread_id, metadata=metadata)


def _parse[Model: BaseModel](
    thread_id: str, metadata: Mapping[str, object], prefix: str, model: type[Model]
) -> dict[str, Model]:
    parsed: dict[str, Model] = {}
    for key, value in metadata.items():
        if not key.startswith(prefix):
            continue
        try:
            parsed[key.removeprefix(prefix)] = model.model_validate(value)
        except ValidationError:
            logger.warning(
                "Dropping an unreadable act-as record",
                extra={"thread_id": thread_id, "metadata_key": key},
                exc_info=True,
            )
    return parsed
