"""Tell an automation's creator when its runs fail to deliver, and pause one that keeps failing.

A run's delivery attempts are kept on its thread as the run makes them, since its calls
may land on any replica; the run's end reads them back once.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Self

from pydantic import JsonValue

from openswe import event_claims
from openswe.run_config import RunConfig
from openswe.schedules.delivery import CreatorDelivery, DeliveryRefused
from openswe.schedules.store import UndeliveredRuns, authorized_schedule_run, record_run_error
from openswe.slack.http import SlackRequestError
from openswe.threads.store import THREADS
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

DELIVERY_TOOLS: Final = frozenset({"slack_post_message", "slack_reply"})
PAUSE_AFTER: Final = 3
_DELIVERED_KEY: Final = "automation_delivered_run_id"
_UNDELIVERED_KEY: Final = "automation_undelivered"
_ENDED_CLAIM: Final = "automation_run_delivery"
_ENDED_CLAIM_TTL: Final = timedelta(days=2)
_ERROR_MAX_CHARS: Final = 500


@dataclass(frozen=True)
class AutomationRunDelivery:
    """Whether one automation run delivered its result."""

    cfg: RunConfig
    thread_id: str
    run_id: str

    @classmethod
    def of(cls, cfg: RunConfig, run_id: str | None) -> Self | None:
        """Track only the invocation stamped by the automation launcher."""
        if (
            not cfg.schedule_id
            or not cfg.thread_id
            or not run_id
            or not cfg.automation_invocation_id
            or cfg.automation_invocation_id != cfg.invocation_id
        ):
            return None
        return cls(cfg=cfg, thread_id=cfg.thread_id, run_id=run_id)

    @staticmethod
    def outcome(content: str | list[JsonValue]) -> bool | str | None:
        """``True`` for a delivery tool's success, its error for a failure, else ``None``."""
        text = (
            content
            if isinstance(content, str)
            else "".join(
                str(block.get("text", "")) if isinstance(block, dict) else str(block)
                for block in content
            )
        )
        try:
            result = json.loads(text)
        except ValueError:
            return None
        if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
            return None
        if result["success"]:
            return True
        error = result.get("error")
        return str(error) if error else "delivery_failed"

    async def tool_finished(self, name: str, content: str | list[JsonValue]) -> None:
        """Record what a delivery tool the run called answered."""
        if name not in DELIVERY_TOOLS:
            return
        outcome = self.outcome(content)
        if outcome is True:
            await THREADS.update(self.thread_id, {_DELIVERED_KEY: self.run_id})
        elif isinstance(outcome, str):
            await self._failed(f"{name}: {outcome}")

    async def _failed(self, error: str) -> None:
        thread = await THREADS.get(self.thread_id)
        earlier = thread.metadata.get(_UNDELIVERED_KEY) if thread is not None else None
        if isinstance(earlier, dict) and earlier.get("run_id") == self.run_id:
            return
        await THREADS.update(
            self.thread_id,
            {_UNDELIVERED_KEY: {"run_id": self.run_id, "error": error[:_ERROR_MAX_CHARS]}},
        )

    async def ended(self) -> None:
        """Settle the run's delivery: the streak it ends or extends, and the creator's notice."""
        thread = await THREADS.get(self.thread_id)
        if thread is None:
            return
        delivered = thread.metadata.get(_DELIVERED_KEY) == self.run_id
        failure = thread.metadata.get(_UNDELIVERED_KEY)
        error = (
            failure.get("error")
            if isinstance(failure, dict) and failure.get("run_id") == self.run_id
            else None
        )
        if not await event_claims.claim(
            _ENDED_CLAIM, f"{self.thread_id}:{self.run_id}", ttl=_ENDED_CLAIM_TTL
        ):
            return
        record = await authorized_schedule_run(self.cfg)
        if record is None:
            return
        streak = UndeliveredRuns(uuid.UUID(record["id"]))
        test_run = thread.metadata.get("schedule_test") is True
        if delivered or not isinstance(error, str):
            if not test_run:
                await streak.record(delivered=True)
            return
        paused = False
        if not test_run and await streak.record(delivered=False) >= PAUSE_AFTER:
            paused = await streak.pause(
                f"Paused after {PAUSE_AFTER} runs in a row failed to deliver: {error}"
            )
        else:
            await record_run_error(record, f"The run failed to deliver its result: {error}")
        logger.warning(
            "An automation run failed to deliver its result",
            extra={
                "schedule_id": record["id"],
                "agent_thread_id": self.thread_id,
                "run_id": self.run_id,
                "automation_paused": paused,
            },
        )
        await self._notify(record.get("name"), error, paused=paused)

    async def _notify(self, name: object, error: str, *, paused: bool) -> None:
        """DM the creator, whatever the automation's prompt says about DMs: Robin sends it."""
        label = name if isinstance(name, str) and name else "Automation"
        quoted = "\n".join(f"> {line}" for line in error.splitlines() or [error])
        lines = [f"Your automation **{label}** ran but couldn't deliver its result:", quoted]
        if paused:
            lines += [
                "",
                f"Robin paused it after {PAUSE_AFTER} runs in a row that didn't deliver. "
                "Fix where it delivers, then turn it back on in Automations.",
            ]
        if url := dashboard_thread_url(self.thread_id):
            lines += ["", f"[Open the run's thread]({url})"]
        try:
            delivery = await CreatorDelivery.for_run(self.cfg)
            await delivery.send("\n".join(lines))
        except DeliveryRefused, SlackRequestError:
            logger.warning(
                "Could not tell an automation's creator its run failed to deliver",
                extra={"schedule_id": self.cfg.schedule_id, "agent_thread_id": self.thread_id},
                exc_info=True,
            )
