"""Robin's daily look across a person's recent threads for recurring work it could take on.

Each weekday one job beats once for every opted-in person. A beat gathers the threads
they were active in, asks a small model whether one playbook fits their work as a
whole, and if one does, proposes it: in the thread that shows the work best when that
thread is theirs, private and recently active, otherwise in a new private thread.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, field_validator
from runtime_api.models import make_model, provider_model_kwargs
from sqlalchemy import select, text

from openswe.dashboard.settings import get_instance_settings
from openswe.database import postgres
from openswe.dispatch import dispatch_agent_run, follow_up_configurable
from openswe.input_messages import InputMessageContext, SystemIdentity
from openswe.jobs.queue import Job
from openswe.notifications.service import AppNotification
from openswe.proactivity.proactivity_engine import ProactiveCandidate, ProactivityEngine
from openswe.prompts import prompt
from openswe.threads.index import IndexedThread, ThreadSearch
from openswe.threads.runs import start_dashboard_thread
from openswe.threads.store import THREADS
from openswe.threads.summary import (
    DASHBOARD_SOURCE,
    thread_is_owner,
    thread_is_private,
    thread_is_unlisted,
    thread_source,
)
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.thread_participants import participant_search_filters

logger = logging.getLogger(__name__)

LOOKBACK: Final = timedelta(days=7)
QUIET_AFTER: Final = timedelta(days=1)
_MAX_THREADS: Final = 20
_SEARCH_PAGE: Final = 50
_SEARCH_SCAN_CAP: Final = 500
_MESSAGES_PER_THREAD: Final = 6
_MESSAGE_CHARS: Final = 600
_DECISION_TIMEOUT_SECONDS: Final = 60
_DECISION_MAX_TOKENS: Final = 1024
_SYSTEM: Final[SystemIdentity] = {
    "id": "system:proactive-heartbeat",
    "display_name": "Robin heartbeat",
    "platform": "open-swe",
}

_RECENT_MESSAGES = text(
    """
    SELECT thread_id, role, text FROM (
        SELECT thread_id, role, text, created_at, message_id,
               row_number() OVER (
                   PARTITION BY thread_id ORDER BY created_at DESC, message_id DESC
               ) AS position
        FROM thread_message
        WHERE thread_id = ANY(:thread_ids) AND namespace = '{}' AND text <> ''
    ) recent
    WHERE position <= :per_thread
    ORDER BY thread_id, created_at, message_id
    """
)


class HeartbeatDecision(BaseModel):
    playbook: str = Field(description="The id of the one playbook to offer, or none.")
    thread_id: str = Field(
        default="", description="The thread that shows this work best, or empty."
    )
    details: str = Field(
        default="",
        description="What the automation should cover for them: topic, channels or repos.",
    )
    reason: str = Field(
        default="", description="One sentence on what in their threads shows the need."
    )

    @field_validator("details", "reason")
    @classmethod
    def _no_markup(cls, value: str) -> str:
        # The proposal fences these in a tag; thread content must not be able to close it.
        return value.replace("<", "").replace(">", "")


@dataclass(frozen=True)
class ProactiveHeartbeat:
    JOB: ClassVar[str] = "proactive_heartbeat"
    # Weekdays at 14:00 UTC: the morning across US time zones.
    SCHEDULE: ClassVar[str] = "0 14 * * 1-5"

    engine: ProactivityEngine
    now: datetime

    @classmethod
    async def run(cls, _job: Job) -> None:
        """Beat once for every person who opted into proactivity."""
        async with postgres.session() as session:
            user_ids = list(
                await session.scalars(
                    select(User.id).where(
                        User.preferences.contains({"experimental_proactivity": True})
                    )
                )
            )
        now = datetime.now(UTC)
        for user_id in user_ids:
            try:
                user = await User.get(user_id)
                if user is not None:
                    await cls(ProactivityEngine.for_user(user), now).beat()
            except Exception:
                logger.exception("Proactive heartbeat failed", extra={"user_id": str(user_id)})

    async def beat(self) -> None:
        threads = await self._recent_threads()
        if not any(thread.updated_at >= self.now - QUIET_AFTER for thread in threads):
            return
        candidates = await self.engine.candidates(threads, self.now)
        if not candidates:
            return
        decision = await self._decide(candidates, threads)
        candidate = next(
            (c for c in candidates if decision is not None and c.playbook.id == decision.playbook),
            None,
        )
        if decision is None or candidate is None:
            return
        thread_id = await self._propose(candidate, decision, threads)
        await self.engine.offered(candidate.playbook, self.now)
        await self._notify(candidate, thread_id)
        logger.info(
            "Proactive heartbeat proposed a playbook",
            extra={"playbook": candidate.playbook.id, "agent_thread_id": thread_id},
        )

    async def _recent_threads(self) -> list[IndexedThread]:
        """The person's own threads active within ``LOOKBACK``, newest activity first."""
        login, threads = self.engine.login, dict[str, IndexedThread]()
        for metadata in participant_search_filters(login, self.engine.user.email or None):
            # Exclusions can't be expressed as a metadata filter, so page until enough pass.
            found = 0
            for offset in range(0, _SEARCH_SCAN_CAP, _SEARCH_PAGE):
                page = await THREADS.search(
                    ThreadSearch(
                        metadata=metadata,
                        sort_by="updated_at",
                        activity_order=True,
                        limit=_SEARCH_PAGE,
                        offset=offset,
                    )
                )
                for thread in page:
                    meta = thread.metadata
                    if (
                        thread.updated_at < self.now - LOOKBACK
                        or thread_is_unlisted(meta)
                        or meta.get("admin_thread") is True
                        or thread_source(meta) == "schedule"
                        or meta.get("thread_category") == "automation"
                        or (thread_is_private(meta) and not thread_is_owner(meta, login))
                    ):
                        continue
                    threads.setdefault(thread.thread_id, thread)
                    found += 1
                if (
                    len(page) < _SEARCH_PAGE
                    or found >= _MAX_THREADS
                    or page[-1].updated_at < self.now - LOOKBACK
                ):
                    break
        ordered = sorted(threads.values(), key=lambda thread: thread.updated_at, reverse=True)
        return ordered[:_MAX_THREADS]

    async def _decide(
        self, candidates: list[ProactiveCandidate], threads: list[IndexedThread]
    ) -> HeartbeatDecision | None:
        excerpts = await self._excerpts([thread.thread_id for thread in threads])
        model_id, effort = (await get_instance_settings()).default_thread_title_model
        kwargs = provider_model_kwargs(model_id, effort, max_tokens=_DECISION_MAX_TOKENS)
        if model_id.startswith("anthropic:"):
            kwargs.pop("thinking", None)
        model = make_model(model_id, **kwargs).with_structured_output(HeartbeatDecision)
        messages = [
            SystemMessage(content=prompt("proactivity/heartbeat", candidates=candidates)),
            HumanMessage(
                content=prompt(
                    "proactivity/heartbeat-threads",
                    threads=[
                        {
                            "id": thread.thread_id,
                            "title": thread.metadata.get("title") or "Untitled thread",
                            "updated": thread.updated_at.date().isoformat(),
                            "resolved": thread.metadata.get("resolved") is True,
                            "messages": excerpts.get(thread.thread_id, []),
                        }
                        for thread in threads
                    ],
                )
            ),
        ]
        # Empty callbacks keep the decision out of any run's message stream.
        config: RunnableConfig = {"callbacks": [], "run_name": "proactive-heartbeat"}
        try:
            async with asyncio.timeout(_DECISION_TIMEOUT_SECONDS):
                result = await model.ainvoke(messages, config=config)
        except Exception:
            logger.warning(
                "Proactive heartbeat decision failed",
                extra={"user_id": str(self.engine.user.id)},
                exc_info=True,
            )
            return None
        return result if isinstance(result, HeartbeatDecision) else None

    @staticmethod
    async def _excerpts(thread_ids: list[str]) -> dict[str, list[dict[str, str]]]:
        """The last top-level messages of each thread, oldest first and cut short."""
        async with postgres.session() as session:
            rows = await session.execute(
                _RECENT_MESSAGES,
                {"thread_ids": thread_ids, "per_thread": _MESSAGES_PER_THREAD},
            )
        excerpts: dict[str, list[dict[str, str]]] = {}
        for thread_id, role, body in rows:
            excerpts.setdefault(thread_id, []).append(
                {"role": "user" if role == "human" else "robin", "text": body[:_MESSAGE_CHARS]}
            )
        return excerpts

    async def _propose(
        self,
        candidate: ProactiveCandidate,
        decision: HeartbeatDecision,
        threads: list[IndexedThread],
    ) -> str:
        """Start the run that makes the offer; the id of the thread it runs in."""
        proposal = prompt(
            "proactivity/proposal",
            playbook=candidate.playbook,
            details=decision.details,
            reason=decision.reason,
        )
        context: InputMessageContext = {
            "sender_id": _SYSTEM["id"],
            "surface": "automation",
            "kind": "system",
        }
        target = next(
            (thread for thread in threads if thread.thread_id == decision.thread_id), None
        )
        if target is not None and self._can_host(target):
            await dispatch_agent_run(
                target.thread_id,
                proposal,
                follow_up_configurable(target.metadata, target.thread_id),
                source=thread_source(target.metadata),
                thread_title=None,
                context=context,
                systems=[_SYSTEM],
                multitask_strategy="enqueue",
            )
            return target.thread_id
        return await start_dashboard_thread(
            self.engine.login,
            self.engine.user.email or None,
            title=f"Suggestion: {candidate.playbook.name}",
            prompt=proposal,
            repos=[],
            visibility="private",
            context=context,
            systems=[_SYSTEM],
        )

    def _can_host(self, thread: IndexedThread) -> bool:
        """Only the person's own private dashboard thread, active recently and not mid-run."""
        meta = thread.metadata
        return (
            thread_source(meta) == DASHBOARD_SOURCE
            and thread_is_private(meta)
            and thread_is_owner(meta, self.engine.login)
            and thread.status == "idle"
            and meta.get("latest_run_status") not in {"pending", "running"}
            and thread.updated_at >= self.now - QUIET_AFTER
        )

    async def _notify(self, candidate: ProactiveCandidate, thread_id: str) -> None:
        url = dashboard_thread_url(thread_id)
        if url is None:
            return
        await AppNotification.send(
            self.engine.user,
            key=f"proactive-offer:{thread_id}:{candidate.playbook.id}",
            title="Robin has a suggestion",
            body=candidate.playbook.offer,
            url=url,
            fallback_text=f"Robin has a suggestion for you: {candidate.playbook.offer} <{url}|Open>",
        )
