import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt
from openswe.slack.client import get_slack_user_info
from openswe.users import User
from openswe.utils.jev import JEV

logger = logging.getLogger(__name__)

WORK_START = time(9)
WORK_END = time(18)
_WORK_DAYS = frozenset(range(5))


class SlackProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str = ""
    real_name: str = ""
    status_text: str = ""
    status_emoji: str = ""
    status_expiration: int = 0


class SlackUserInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    real_name: str = ""
    tz: str = ""
    profile: SlackProfile = Field(default_factory=SlackProfile)

    @property
    def display_name(self) -> str:
        return (
            self.profile.display_name.strip()
            or self.profile.real_name.strip()
            or self.real_name.strip()
            or self.name.strip()
        )

    async def out_of_office(self, now: datetime) -> bool | None:
        names = (self.profile.display_name, self.profile.real_name, self.real_name, self.name)
        if any(re.search(r"\booo\b", name, re.I) for name in names):
            return True
        status = self.profile
        if status.status_expiration and status.status_expiration <= now.timestamp():
            return False
        if not status.status_text.strip() and not status.status_emoji.strip():
            return False
        choice = await JEV.choose_one(
            status.model_dump_json(include={"status_text", "status_emoji"}),
            question="out_of_office",
            instructions=prompt("slack/availability-instructions", now=now.isoformat()),
            criteria={
                "out_of_office": prompt("slack/availability-away"),
                "not_out_of_office": prompt("slack/availability-present"),
            },
        )
        return None if choice is None else choice == "out_of_office"


@dataclass(frozen=True, slots=True)
class ReviewerAvailability:
    """Slack out-of-office status and Mon–Fri working hours in the person's timezone."""

    zone: ZoneInfo | None
    out_of_office: bool | None = False

    @property
    def zone_name(self) -> str:
        return self.zone.key if self.zone is not None else ""

    def on_shift(self, now: datetime) -> bool:
        if self.zone is None:
            return True
        local = now.astimezone(self.zone)
        return local.weekday() in _WORK_DAYS and WORK_START <= local.time() < WORK_END

    def next_start(self, now: datetime) -> datetime:
        """``now`` while on shift, otherwise when the next work day starts."""
        if self.zone is None or self.on_shift(now):
            return now
        local = now.astimezone(self.zone)
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            start = datetime.combine(day, WORK_START, tzinfo=self.zone)
            if day.weekday() in _WORK_DAYS and start > local:
                return start.astimezone(UTC)
        raise AssertionError("a work day starts within a week")

    @classmethod
    async def for_user(cls, user: User) -> Self:
        if not user.slack_user_id:
            return cls(None)
        info = await get_slack_user_info(user.slack_user_id)
        if info is None:
            return cls(None, None)
        profile = SlackUserInfo.model_validate(info)
        away = await profile.out_of_office(datetime.now(UTC))
        name = profile.tz
        if not name:
            return cls(None, away)
        try:
            return cls(ZoneInfo(name), away)
        except ZoneInfoNotFoundError, ValueError:
            logger.warning(
                "Unknown Slack timezone; treating the person as always on shift",
                extra={"slack_user_id": user.slack_user_id, "slack_timezone": name},
            )
            return cls(None, away)
