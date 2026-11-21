"""Which automations Robin may offer a person, and the automation an accepted offer creates.

A playbook may be offered until the person has its ``Robin: …`` automation, and not again
within ``OFFER_COOLDOWN`` of the last time it was offered.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self

from openswe.proactivity.playbooks import Playbook
from openswe.prompts import prompt
from openswe.schedules import store as schedules
from openswe.threads.index import IndexedThread
from openswe.users import User, UserPreferencesPatch

logger = logging.getLogger(__name__)

OFFER_COOLDOWN = timedelta(days=14)


class OfferRefused(Exception):
    """The engine does not allow this offer."""


@dataclass(frozen=True)
class ProactiveCandidate:
    playbook: Playbook
    # What the engine found in the person's threads; empty when only their content can show it.
    evidence: str


@dataclass(frozen=True)
class ProactivityEngine:
    user: User
    login: str

    @classmethod
    async def for_login(cls, login: str) -> Self | None:
        """The engine for this person, once they opted into proactivity."""
        user = await User.for_login("github", login) if login else None
        if user is None or not user.typed_preferences.experimental_proactivity:
            return None
        return cls(user, login)

    @classmethod
    def for_user(cls, user: User) -> Self:
        return cls(user, user.github_login or f"user:{user.id}")

    async def candidates(
        self, threads: list[IndexedThread], now: datetime
    ) -> list[ProactiveCandidate]:
        """Playbooks the person has no automation for, past their cooldown, with evidence."""
        automations = await self._automation_names()
        offered_at = self.user.typed_preferences.proactive_offered_at
        candidates: list[ProactiveCandidate] = []
        for playbook in await Playbook.catalog():
            last = offered_at.get(playbook.id)
            if playbook.automation_name in automations or (
                last and now - datetime.fromisoformat(last) < OFFER_COOLDOWN
            ):
                continue
            evidence = playbook.evidence_in(threads, now)
            if evidence is not None:
                candidates.append(ProactiveCandidate(playbook, evidence))
        return candidates

    async def offered(self, playbook: Playbook, now: datetime) -> None:
        offered_at = {
            **self.user.typed_preferences.proactive_offered_at,
            playbook.id: now.isoformat(),
        }
        await self.user.merge_preferences(UserPreferencesPatch(proactive_offered_at=offered_at))

    async def accepted(
        self,
        playbook_id: str,
        *,
        details: str,
        cron: str | None = None,
        timezone: str = "UTC",
    ) -> dict[str, object]:
        """Create the playbook's automation for this person."""
        playbook = await Playbook.named(playbook_id)
        if playbook is None:
            raise OfferRefused(f"no playbook named {playbook_id}")
        if playbook.automation_name in await self._automation_names():
            raise OfferRefused(f"{playbook.automation_name} already exists")
        return await schedules.create_agent_schedule(
            self.login,
            schedules.ScheduleCreateBody(
                prompt=prompt(
                    "proactivity/automation", run_prompt=playbook.run_prompt, details=details
                ),
                triggers=[schedules.ScheduleTrigger(cron=cron or playbook.cron, timezone=timezone)],
                name=playbook.automation_name,
            ),
            email=self.user.email or None,
        )

    async def _automation_names(self) -> list[str]:
        try:
            automations = await schedules.list_agent_schedules(self.login)
        except Exception:
            logger.warning("Could not list automations for proactivity", exc_info=True)
            return []
        return [
            str(automation["name"])
            for automation in automations
            if automation.get("system") is not True and automation.get("name")
        ]
