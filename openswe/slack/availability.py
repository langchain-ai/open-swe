import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from openswe.prompts import prompt
from openswe.utils.jev import select_jev_choice


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
        choice = await select_jev_choice(
            status.model_dump_json(include={"status_text", "status_emoji"}),
            question="out_of_office",
            instructions=prompt("slack/availability-instructions", now=now.isoformat()),
            criteria={
                "out_of_office": prompt("slack/availability-away"),
                "not_out_of_office": prompt("slack/availability-present"),
            },
        )
        return None if choice is None else choice == "out_of_office"
