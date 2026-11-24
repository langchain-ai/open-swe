"""A per-thread budget for the GitHub API calls sandboxes make with the App installation's token.

Every sandbox spends the installation's one hourly quota, which Robin's own reviews and
approvals spend too. While the instance settings' ``sandbox_github_budget_mode`` is
``observe`` or ``enforce``, a sandbox's proxy config gives ``api.github.com`` its own callback
that LangSmith's proxy calls for every request (``SandboxProxyCallback``), and the backend
counts each call against the thread and logs it before answering with the token. ``git``
over HTTPS to ``github.com`` spends none of the API quota and is never metered.

``enforce`` refuses a call once the thread made ``sandbox_github_budget_per_hour`` in the last
60 minutes, and every call while the installation brake holds: the quota sampler sets it when
less than ``sandbox_github_brake_percent`` of an hourly quota is left, until that quota resets.
The proxy turns a refusal into an opaque ``502 callback resolution failed``, so the thread is
also sent a note saying why and when calls work again. Anything that goes wrong while counting,
such as the database being down, lets the call through.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, get_args
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe import event_claims
from openswe.dashboard.settings import SandboxGitHubBudgetMode
from openswe.dashboard.settings_cache import cached_instance_settings
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.app import GITHUB_APP_INSTALLATION_ID
from openswe.input_messages import system_input
from openswe.jobs.queue import Job
from openswe.prompts import prompt
from openswe.users.models import User
from openswe.utils.thread_ops import queue_message_for_thread

logger = logging.getLogger(__name__)

Resource = Literal["core", "graphql"]
RefusalReason = Literal["cap", "brake"]

METERED_HOST: Final = "api.github.com"
WINDOW: Final = timedelta(hours=1)
_BRAKE_SCOPE: Final = "github-installation-brake"
_NOTE_SCOPE: Final = "sandbox-github-budget-note"
PURGE_JOB: Final = "sandbox_github_call_purge"
PURGE_SCHEDULE: Final = "*/10 * * * *"


class SandboxGitHubBudget(BaseModel):
    """The deployment's budget for sandboxes' GitHub API calls on the installation's token."""

    model_config = ConfigDict(frozen=True)

    mode: SandboxGitHubBudgetMode = "off"
    per_hour: int = 500
    brake_percent: int = 20

    @classmethod
    async def configured(cls) -> SandboxGitHubBudget:
        """The budget an instance admin set; a value it cannot read leaves metering off."""
        settings = await cached_instance_settings()
        try:
            return cls.model_validate(
                {
                    "mode": settings.get("sandbox_github_budget_mode"),
                    "per_hour": settings.get("sandbox_github_budget_per_hour"),
                    "brake_percent": settings.get("sandbox_github_brake_percent"),
                }
            )
        except ValueError:
            logger.warning("Sandbox GitHub budget is misconfigured; metering is off", exc_info=True)
            return cls()

    @classmethod
    async def meters(cls) -> bool:
        """Whether sandboxes' proxy configs send their API calls through the metering callback."""
        return (await cls.configured()).mode != "off"

    async def admit(self, thread_id: str, call: ProxiedCall) -> Refusal | None:
        """Count ``call`` against ``thread_id``; the refusal, when enforce turns it away."""
        resource = call.resource()
        # A brake engaged before an admin turned it off (0%) must not outlive that change.
        brake_until = await self.brake_until(resource) if self.brake_percent else None
        async with postgres.session() as session:
            # Parallel calls of one thread must not all pass the cap on the same count.
            await session.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(thread_id, 0)))
            )
            window = await SandboxGitHubCalls.window(thread_id, session=session)
            refusal = None
            if brake_until is not None:
                refusal = Refusal(reason="brake", until=brake_until, per_hour=self.per_hour)
            elif window.total >= self.per_hour:
                refusal = Refusal(
                    reason="cap", until=window.frees_at(self.per_hour), per_hour=self.per_hour
                )
            refused = refusal is not None and self.mode == "enforce"
            if not refused:
                await SandboxGitHubCalls.record(thread_id, session=session)
        logger.info(
            "Sandbox GitHub API call",
            extra={
                "agent_thread_id": thread_id,
                "github_method": call.method,
                "github_route": call.path,
                "github_quota_resource": resource,
                "sandbox_github_budget_mode": self.mode,
                "sandbox_github_calls_in_window": window.total + (0 if refused else 1),
                "sandbox_github_over_budget": None if refusal is None else refusal.reason,
                "sandbox_github_refused": refused,
            },
        )
        return refusal if refused else None

    @staticmethod
    async def brake_until(resource: Resource) -> datetime | None:
        """When the installation brake on ``resource`` lifts; None when it does not hold."""
        left = await event_claims.remaining(
            _BRAKE_SCOPE, f"{GITHUB_APP_INSTALLATION_ID}:{resource}"
        )
        return datetime.now(UTC) + timedelta(seconds=left) if left > 0 else None

    async def sampled(
        self, resource: str, *, limit: int, remaining: int, reset_at: datetime
    ) -> None:
        """Hold the brake on ``resource`` until ``reset_at`` once too little of it is left."""
        if resource not in get_args(Resource) or limit <= 0:
            return
        if remaining * 100 >= limit * self.brake_percent:
            return
        ttl = reset_at - datetime.now(UTC)
        if ttl <= timedelta(0):
            return
        key = f"{GITHUB_APP_INSTALLATION_ID}:{resource}"
        if await event_claims.claim(_BRAKE_SCOPE, key, ttl=ttl):
            logger.warning(
                "GitHub installation brake engaged",
                extra={
                    "github_installation_id": GITHUB_APP_INSTALLATION_ID,
                    "github_quota_resource": resource,
                    "github_quota_limit": limit,
                    "github_quota_remaining": remaining,
                    "github_quota_reset_at": reset_at.isoformat(),
                    "sandbox_github_budget_mode": self.mode,
                },
            )


class ProxiedCall(BaseModel):
    """The sandbox's request the proxy asks about (``full_request``)."""

    method: str = ""
    path: str = ""

    def resource(self) -> Resource:
        return "graphql" if self.path.rstrip("/") == "/graphql" else "core"


class Refusal(BaseModel):
    """Why a thread's call was refused, and when calls work again."""

    reason: RefusalReason
    until: datetime
    per_hour: int

    async def tell(self, thread_id: str) -> None:
        """Send the thread a note about the refusal, once until calls work again."""
        ttl = max(self.until - datetime.now(UTC), timedelta(minutes=1))
        if not await event_claims.claim(_NOTE_SCOPE, f"{thread_id}:{self.reason}", ttl=ttl):
            return
        note = prompt(
            "runs/sandbox-github-budget",
            reason=self.reason,
            per_hour=self.per_hour,
            until=self.until.astimezone(UTC).strftime("%H:%M UTC"),
        )
        message = system_input(
            note, {"sender_id": "system:github-budget", "surface": "automation", "kind": "system"}
        )
        await queue_message_for_thread(thread_id, dict(message))


@dataclass(frozen=True)
class CallWindow:
    """A thread's metered calls in the last ``WINDOW``, by minute, oldest first."""

    minutes: tuple[tuple[datetime, int], ...]

    @property
    def total(self) -> int:
        return sum(calls for _minute, calls in self.minutes)

    def frees_at(self, cap: int) -> datetime:
        """When enough of the oldest calls leave the window for the thread to be under ``cap``."""
        left = self.total
        for minute, calls in self.minutes:
            left -= calls
            if left < cap:
                return minute + timedelta(minutes=1) + WINDOW
        return datetime.now(UTC)


class SandboxGitHubCalls(Base):
    """How many metered GitHub API calls a thread's sandbox made in one minute."""

    __tablename__ = "sandbox_github_call"

    # The Agent Server thread the sandbox is bound to.
    thread_id: Mapped[str]
    minute: Mapped[datetime]
    calls: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @staticmethod
    def oldest_minute() -> datetime:
        """The start of the oldest minute whose calls may still be in the window."""
        return datetime.now(UTC) - WINDOW - timedelta(minutes=1)

    @classmethod
    async def record(cls, thread_id: str, *, session: AsyncSession | None = None) -> None:
        minute = datetime.now(UTC).replace(second=0, microsecond=0)
        statement = insert(cls).values(id=uuid7(), thread_id=thread_id, minute=minute, calls=1)
        async with postgres.session(session) as db:
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=[cls.thread_id, cls.minute],
                    set_={"calls": cls.calls + 1, "updated_at": NOW},
                )
            )

    @classmethod
    async def window(cls, thread_id: str, *, session: AsyncSession | None = None) -> CallWindow:
        async with postgres.session(session) as db:
            rows = await db.execute(
                select(cls.minute, cls.calls)
                .where(cls.thread_id == thread_id, cls.minute > cls.oldest_minute())
                .order_by(cls.minute)
            )
            return CallWindow(tuple((minute, calls) for minute, calls in rows.tuples()))

    @classmethod
    async def purge(cls, _job: Job) -> None:
        """The ``sandbox_github_call_purge`` job: drop minutes no window reaches any more."""
        async with postgres.session() as session:
            await session.execute(delete(cls).where(cls.minute <= cls.oldest_minute()))
